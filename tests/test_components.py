import sqlite3
from pathlib import Path

import pytest

from backend.components.example_retriever import ExampleRetriever
from backend.components.query_classifier import QueryClassifier
from backend.components.result_formatter import ResultFormatter
from backend.components.schema_linker import SchemaLinker
from backend.components.sql_executor import SqlExecutor
from backend.components.sql_generator import GenerationError, SqlGenerator
from backend.schemas import ExecutorOutput, GeneratorInput, ExampleIn
from backend.store import AppStore

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.execute("INSERT INTO orders (order_id, order_date, total_amount, status) VALUES (1, '2026-01-01', 10, 'pending')")
    conn.execute("INSERT INTO few_shot_examples (question, sql, query_type) VALUES ('q1', 'SELECT 1', 'SELECT_SIMPLE')")
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize("q,expected", [
    ("Delete all orders", "UNSUPPORTED"),
    ("Orders in the last 30 days", "SELECT_TEMPORAL"),
    ("Count orders by status", "SELECT_AGGREGATE"),
    ("Show all customers from Mumbai", "SELECT_SIMPLE"),
])
def test_classifier(q, expected):
    assert QueryClassifier().classify(q).query_type == expected


def test_linker():
    out = SchemaLinker().link("revenue per customer")
    assert {"customers", "orders"} <= set(out.resolved_tables)
    assert SchemaLinker().link("zzz").resolved_tables == ["orders"]


def test_retriever(tmp_path):
    store = AppStore(tmp_path / "app.db", tmp_path / "none.db").init()
    store.create_example(ExampleIn(question="q1", sql="SELECT 1"))
    store.create_example(ExampleIn(question="q2", sql="SELECT 2"))
    out = ExampleRetriever(store).retrieve("anything", top_k=1)
    assert [e.question for e in out.examples] == ["q1"]

def test_generator_matches_and_fails_closed():
    gen = SqlGenerator()
    assert "GROUP BY status" in gen.generate(GeneratorInput(nl_query="Count orders by status")).sql
    with pytest.raises(GenerationError):
        gen.generate(GeneratorInput(nl_query="what is the meaning of life"))


def test_executor_ok_and_read_only(db):
    ex = SqlExecutor(db)
    out = ex.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
    assert out.error is None and out.columns == ["status", "n"] and out.rows == [["pending", 1]]
    assert ex.execute("DELETE FROM orders").error is not None
    assert ex.execute("SELECT COUNT(*) FROM orders").rows == [[1]]


def test_formatter():
    out = ResultFormatter().format(ExecutorOutput(rows=[["a", 1]], columns=["k", "v"], row_count=1), "q")
    assert out.table == [{"k": "a", "v": 1}] and out.row_count == 1


def test_executor_truncates_to_max_rows(db):
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO orders (order_id, order_date, status) VALUES (?, '2026-01-01', 'x')", [(i,) for i in range(2, 50)])
    conn.commit()
    conn.close()
    out = SqlExecutor(db).execute("SELECT order_id FROM orders", max_rows=10)
    assert out.row_count == 10 and out.truncated
    assert not SqlExecutor(db).execute("SELECT order_id FROM orders", max_rows=100).truncated


def test_executor_timeout_on_runaway_query(db):
    ex = SqlExecutor(db, timeout_s=0.3)
    out = ex.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")
    assert out.error and "time limit" in out.error and out.latency_ms < 3000


@pytest.mark.parametrize("sql", [
    "ATTACH DATABASE ':memory:' AS x",
    "PRAGMA writable_schema = 1",
    "CREATE TABLE t (a INT)",
    "INSERT INTO orders (order_id, order_date) VALUES (99, 'x')",
])
def test_executor_authorizer_denies_non_select(db, sql):
    assert SqlExecutor(db).execute(sql).error is not None
