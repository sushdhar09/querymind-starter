from backend.components.query_classifier import QueryClassifier
from backend.components.result_formatter import ResultFormatter
from backend.pipeline import Pipeline
from backend.schemas import (
    Ambiguity, ExecutorOutput, GeneratorInput, GeneratorOutput, LinkerOutput, QueryRequest, RetrieverOutput, ValidatorOutput,
)


class FakeLinker:
    def __init__(self, ambiguities=()):
        self.ambiguities, self.choices = list(ambiguities), None

    def link(self, nl_query, choices=None):
        self.choices = choices
        return LinkerOutput(ambiguities=self.ambiguities)


class FakeRetriever:
    def retrieve(self, nl_query, top_k=3):
        return RetrieverOutput()


class FakeGenerator:
    def __init__(self, *sqls):
        self.sqls, self.calls = list(sqls), []

    def generate(self, req: GeneratorInput):
        self.calls.append(req)
        return GeneratorOutput(sql=self.sqls.pop(0), explanation="fake")


class FakeValidator:
    def validate(self, sql, max_rows=100, dialect="sqlite"):
        ok = sql.upper().startswith("SELECT")
        return ValidatorOutput(valid=ok, sanitized_sql=sql if ok else "", errors=[] if ok else ["not a select"])


class FakeExecutor:
    def __init__(self, out=None):
        self.out, self.executed = out or ExecutorOutput(rows=[[1]], columns=["n"], row_count=1), []

    def execute(self, sql, max_rows=None):
        self.executed.append(sql)
        return self.out


def make(generator, executor=None, linker=None):
    executor = executor or FakeExecutor()
    p = Pipeline(QueryClassifier(), linker or FakeLinker(), FakeRetriever(), generator, FakeValidator(), executor, ResultFormatter())
    return p, executor


def run(p, q="Count orders"):
    return p.run(QueryRequest(nl_query=q))


def test_success():
    p, ex = make(FakeGenerator("SELECT 1"))
    r = run(p)
    assert r.status == "success" and r.generated_sql == "SELECT 1" and r.result.table == [{"n": 1}] and ex.executed == ["SELECT 1"]


def test_unsupported_never_reaches_generator_or_executor():
    gen = FakeGenerator("SELECT 1")
    p, ex = make(gen)
    r = run(p, "Delete all orders")
    assert r.status == "blocked" and r.failed_stage == "classifier" and not gen.calls and not ex.executed


def test_validation_retry_gets_feedback():
    gen = FakeGenerator("DROP TABLE x", "SELECT 2")
    p, ex = make(gen)
    r = run(p)
    assert r.status == "success" and ex.executed == ["SELECT 2"] and gen.calls[1].feedback == "not a select"


def test_validation_failure_after_retry_blocks_and_does_not_execute():
    p, ex = make(FakeGenerator("DROP TABLE x", "DELETE FROM x"))
    r = run(p)
    assert r.status == "blocked" and r.failed_stage == "validator" and not ex.executed and r.generated_sql == "DELETE FROM x"


def test_generator_failure_fails_closed():
    class Boom:
        def generate(self, req):
            raise RuntimeError("llm down")

    p, ex = make(Boom())
    r = run(p)
    assert r.status == "error" and r.failed_stage == "generator" and r.error == "llm down" and not ex.executed


def test_executor_error_reported():
    p, _ = make(FakeGenerator("SELECT 1"), FakeExecutor(ExecutorOutput(error="no such table")))
    r = run(p)
    assert r.status == "error" and r.failed_stage == "executor" and r.error == "no such table"


AMBIGUITY = Ambiguity(term="revenue", candidates=["orders.total_amount", "payments.amount"], message="'revenue' is ambiguous.")


def test_ambiguity_does_not_block_by_default():
    p, ex = make(FakeGenerator("SELECT 1"), linker=FakeLinker([AMBIGUITY]))
    r = run(p, "revenue by month")
    assert r.status == "success" and r.resolved_entities.ambiguities[0].term == "revenue"


def test_clarification_requested_before_generation():
    gen = FakeGenerator("SELECT 1")
    p, ex = make(gen, linker=FakeLinker([AMBIGUITY]))
    r = p.run(QueryRequest(nl_query="revenue by month", clarify_ambiguities=True))
    assert r.status == "needs_clarification" and "ambiguous" in r.error and not gen.calls and not ex.executed


def test_column_choices_are_forwarded_to_linker():
    linker = FakeLinker()
    p, _ = make(FakeGenerator("SELECT 1"), linker=linker)
    p.run(QueryRequest(nl_query="revenue by month", clarify_ambiguities=True, column_choices={"revenue": "orders.total_amount"}))
    assert linker.choices == {"revenue": "orders.total_amount"}
