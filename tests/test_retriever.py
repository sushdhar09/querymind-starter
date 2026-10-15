import json
from pathlib import Path

import pytest

from backend.components.example_retriever import ExampleRetriever
from backend.components.sql_executor import SqlExecutor
from backend.components.sql_validator import SqlValidator
from backend.config import DB_PATH
from backend.embeddings import HashEmbedding
from backend.schemas import ExampleIn
from backend.store import AppStore

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "examples_seed.json"


class CountingEmbedding(HashEmbedding):
    calls = 0

    @staticmethod
    def name() -> str:
        return "querymind-hash"

    def __call__(self, input):
        CountingEmbedding.calls += len(input)
        return super().__call__(input)


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db", examples_seed=SEED).init()


@pytest.fixture
def retriever(store, tmp_path):
    return ExampleRetriever(store, tmp_path / "chroma", HashEmbedding())


def test_returns_most_similar_examples_with_scores(retriever):
    out = retriever.retrieve("customers from Delhi", top_k=3)
    assert len(out.examples) == 3
    assert out.examples[0].question == "List all customers from Delhi"
    scores = [e.score for e in out.examples]
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[-1]


def test_different_questions_retrieve_different_examples(retriever):
    assert retriever.retrieve("orders shipped late", 1).examples[0].question != retriever.retrieve("failed payments", 1).examples[0].question
    assert "failed payments" in retriever.retrieve("failed payments", 1).examples[0].question.lower()


def test_query_type_filter(retriever):
    results = retriever.search("orders per month", top_k=5, query_type="SELECT_TEMPORAL")
    assert results and all("strftime" in r.sql or "date(" in r.sql.lower() or "julianday" in r.sql for r in results)


def test_sync_reflects_create_update_delete_immediately(store, retriever):
    ex = store.create_example(ExampleIn(question="zebra crossing statistics", sql="SELECT 1"))
    assert retriever.retrieve("zebra crossing statistics", 1).examples[0].sql == "SELECT 1"
    store.update_example(ex.id, ExampleIn(question="zebra crossing statistics", sql="SELECT 2"))
    assert retriever.retrieve("zebra crossing statistics", 1).examples[0].sql == "SELECT 2"
    store.delete_example(ex.id)
    assert all(e.question != "zebra crossing statistics" for e in retriever.retrieve("zebra crossing statistics", 3).examples)


def test_sync_only_embeds_changed_examples(store, tmp_path):
    CountingEmbedding.calls = 0
    r = ExampleRetriever(store, tmp_path / "chroma", CountingEmbedding())
    r.sync()
    first = CountingEmbedding.calls
    assert first >= 42
    CountingEmbedding.calls = 0
    assert r.sync() == 0 and CountingEmbedding.calls == 0
    store.create_example(ExampleIn(question="one more example", sql="SELECT 1"))
    CountingEmbedding.calls = 0
    assert r.sync() == 1 and CountingEmbedding.calls == 1


def test_index_persists_across_instances(store, tmp_path):
    ExampleRetriever(store, tmp_path / "chroma", HashEmbedding()).sync()
    CountingEmbedding.calls = 0
    again = ExampleRetriever(store, tmp_path / "chroma", CountingEmbedding())
    assert again.sync() == 0 and again.retrieve("customers from Delhi", 1).examples


def test_empty_store_returns_nothing(tmp_path):
    s = AppStore(tmp_path / "app.db").init()
    assert ExampleRetriever(s, tmp_path / "chroma", HashEmbedding()).retrieve("anything").examples == []


def test_falls_back_to_lexical_when_embedding_fails(store, tmp_path):
    class Broken(HashEmbedding):
        @staticmethod
        def name() -> str:
            return "querymind-hash"

        def __call__(self, input):
            raise RuntimeError("model unavailable")

    out = ExampleRetriever(store, tmp_path / "chroma", Broken()).retrieve("customers from Delhi", 2)
    assert out.examples[0].question == "List all customers from Delhi"


def test_seeding_runs_once_and_respects_deletions(tmp_path):
    s = AppStore(tmp_path / "app.db", examples_seed=SEED).init()
    n = len(s.list_examples(limit=1000))
    assert n >= 30
    s.delete_example(s.list_examples()[0].id)
    s.init()
    assert len(s.list_examples(limit=1000)) == n - 1


def test_examples_version_bumps_on_mutation(tmp_path):
    s = AppStore(tmp_path / "app.db").init()
    v0 = s.examples_version()
    ex = s.create_example(ExampleIn(question="q", sql="SELECT 1"))
    s.update_example(ex.id, ExampleIn(question="q2", sql="SELECT 1"))
    s.delete_example(ex.id)
    assert s.examples_version() == v0 + 3
    s.delete_example(ex.id)
    assert s.examples_version() == v0 + 3


def test_example_library_quality():
    examples = json.loads(SEED.read_text())
    assert len(examples) >= 30 and len({e["question"] for e in examples}) == len(examples)
    assert {e["query_type"] for e in examples} == {"SELECT_SIMPLE", "SELECT_AGGREGATE", "SELECT_JOIN", "SELECT_TEMPORAL"}


def test_example_library_does_not_leak_test_set():
    import sqlglot
    def norm(s):
        return sqlglot.parse_one(s, read="sqlite").sql(dialect="sqlite")

    tests = json.loads((ROOT / "eval" / "test_queries.json").read_text())
    gt_sql = {norm(t["ground_truth_sql"]) for t in tests}
    gt_q = {t["natural_language_query"].lower() for t in tests}
    for e in json.loads(SEED.read_text()):
        assert e["question"].lower() not in gt_q and norm(e["sql"]) not in gt_sql, e["question"]


@pytest.mark.skipif(not DB_PATH.exists(), reason="run data/seed_db.py first")
def test_every_example_validates_and_returns_rows():
    v, ex = SqlValidator(), SqlExecutor()
    for e in json.loads(SEED.read_text()):
        out = v.validate(e["sql"])
        assert out.valid, (e["question"], out.errors)
        res = ex.execute(out.sanitized_sql)
        assert res.error is None and res.row_count > 0, (e["question"], res.error)


@pytest.mark.skipif(not (Path.home() / ".cache" / "chroma" / "onnx_models").exists(), reason="ONNX model not downloaded")
def test_real_embedding_model_handles_paraphrases(store, tmp_path):
    r = ExampleRetriever(store, tmp_path / "chroma-onnx")
    assert r.retrieve("which clients live in Delhi", 1).examples[0].question == "List all customers from Delhi"
    top = [e.question for e in r.retrieve("how much did each payment method bring in", 3).examples]
    assert "How much money was collected by each payment method?" in top
