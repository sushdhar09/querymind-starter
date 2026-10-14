import logging
import time
from typing import Optional, Protocol

from backend.schemas import (
    ClassifierOutput, ExecutorOutput, FormatterOutput, GeneratorInput, GeneratorOutput, LinkerOutput,
    PipelineResponse, QueryRequest, RetrieverOutput, ValidatorOutput,
)

log = logging.getLogger(__name__)


class Classifier(Protocol):
    def classify(self, nl_query: str) -> ClassifierOutput: ...


class Linker(Protocol):
    def link(self, nl_query: str, choices: Optional[dict[str, str]] = None) -> LinkerOutput: ...


class Retriever(Protocol):
    def retrieve(self, nl_query: str, top_k: int = 3) -> RetrieverOutput: ...


class Generator(Protocol):
    def generate(self, req: GeneratorInput) -> GeneratorOutput: ...


class Validator(Protocol):
    def validate(self, sql: str, max_rows: int = 100, dialect: str = "sqlite") -> ValidatorOutput: ...


class Executor(Protocol):
    def execute(self, sql: str, max_rows: Optional[int] = None) -> ExecutorOutput: ...


class Formatter(Protocol):
    def format(self, execution: ExecutorOutput, nl_query: str) -> FormatterOutput: ...


class StageError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


class Pipeline:
    def __init__(self, classifier: Classifier, linker: Linker, retriever: Retriever, generator: Generator,
                 validator: Validator, executor: Executor, formatter: Formatter, self_heal: bool = True):
        self.self_heal = self_heal
        self.classifier, self.linker, self.retriever = classifier, linker, retriever
        self.generator, self.validator, self.executor, self.formatter = generator, validator, executor, formatter

    def run(self, req: QueryRequest) -> PipelineResponse:
        start = time.time()
        resp = PipelineResponse(status="error", natural_language_query=req.nl_query)
        try:
            self._run(req, resp)
        except StageError as e:
            log.warning("Pipeline stage '%s' failed: %s", e.stage, e)
            resp.status, resp.error, resp.failed_stage = "error", str(e), e.stage
        except Exception:
            log.exception("Unexpected pipeline failure")
            resp.status, resp.error, resp.failed_stage = "error", "Internal error", "unknown"
        resp.total_latency_ms = int((time.time() - start) * 1000)
        return resp

    def _run(self, req: QueryRequest, resp: PipelineResponse) -> None:
        classified = self._stage("classifier", self.classifier.classify, req.nl_query)
        resp.query_type = classified.query_type
        if classified.query_type == "UNSUPPORTED":
            resp.status, resp.failed_stage = "blocked", "classifier"
            resp.error = classified.reason or "Unsupported query type"
            return

        resp.resolved_entities = self._stage("linker", self.linker.link, req.nl_query, req.column_choices)
        if req.clarify_ambiguities and resp.resolved_entities.ambiguities:
            resp.status, resp.failed_stage = "needs_clarification", "linker"
            resp.error = resp.resolved_entities.ambiguities[0].message
            return
        resp.few_shot_examples = self._stage("retriever", self.retriever.retrieve, req.nl_query, 3).examples

        gen_in = GeneratorInput(nl_query=req.nl_query, dialect=req.dialect, linker=resp.resolved_entities,
                                examples=resp.few_shot_examples)
        validated = self._generate_and_validate(gen_in, req.max_rows, resp)
        if not validated.valid:
            resp.status, resp.failed_stage = "blocked", "validator"
            resp.error = "; ".join(validated.errors) or "SQL failed validation"
            return

        resp.generated_sql = validated.sanitized_sql
        resp.execution = self._stage("executor", self.executor.execute, validated.sanitized_sql, req.max_rows)
        if resp.execution.error and self.self_heal:
            self._self_heal(gen_in, req, resp)
        if resp.execution.error:
            resp.status, resp.failed_stage, resp.error = "error", "executor", resp.execution.error
            return

        resp.result = self._stage("formatter", self.formatter.format, resp.execution, req.nl_query)
        resp.status = "success"

    def _generate_and_validate(self, gen_in: GeneratorInput, max_rows: int, resp: PipelineResponse) -> ValidatorOutput:
        generated = self._stage("generator", self.generator.generate, gen_in)
        validated = self._stage("validator", self.validator.validate, generated.sql, max_rows, gen_in.dialect)
        if not validated.valid:
            resp.generated_sql = generated.sql
            retry_in = gen_in.model_copy(update={"feedback": "; ".join(validated.errors), "previous_sql": generated.sql})
            generated = self._stage("generator", self.generator.generate, retry_in)
            validated = self._stage("validator", self.validator.validate, generated.sql, max_rows, gen_in.dialect)
        resp.explanation = generated.explanation
        if not validated.valid:
            resp.generated_sql = generated.sql
        return validated

    def _self_heal(self, gen_in: GeneratorInput, req: QueryRequest, resp: PipelineResponse) -> None:
        previous = resp.generated_sql
        retry_in = gen_in.model_copy(update={"feedback": f"Execution error: {resp.execution.error}", "previous_sql": previous})
        try:
            generated = self.generator.generate(retry_in)
            if generated.sql.strip().lower() == previous.strip().lower():
                return
            validated = self.validator.validate(generated.sql, req.max_rows, req.dialect)
            if not validated.valid:
                return
            execution = self.executor.execute(validated.sanitized_sql, req.max_rows)
        except Exception as e:
            log.warning("Self-healing attempt failed: %s", e)
            return
        if not execution.error:
            resp.generated_sql, resp.execution, resp.explanation, resp.self_healed = (
                validated.sanitized_sql, execution, generated.explanation, True)

    @staticmethod
    def _stage(name, fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            raise StageError(name, str(e) or type(e).__name__) from e
