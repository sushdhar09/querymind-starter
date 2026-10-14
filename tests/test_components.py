import sqlite3
from pathlib import Path

import pytest

from backend.components.example_retriever import ExampleRetriever
from backend.schema_index import SchemaIndex
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


@pytest.fixture
def index(db, tmp_path):
    store = AppStore(tmp_path / "app.db", tmp_path / "none.db").init()
    return SchemaIndex(store, db)


@pytest.mark.parametrize("q,expected", [
    ("Delete all orders", "UNSUPPORTED"),
    ("Show updated orders", "SELECT_SIMPLE"),
    ("Orders in the last 30 days", "SELECT_TEMPORAL"),
    ("Count orders by status", "SELECT_AGGREGATE"),
    ("Show all customers from Mumbai", "SELECT_SIMPLE"),
    ("List orders with customer names", "SELECT_JOIN"),
])
def test_classifier(q, expected, index):
    assert QueryClassifier(index).classify(q).query_type == expected


def test_classifier_without_index_still_classifies():
    out = QueryClassifier().classify("Count orders by status")
    assert out.query_type == "SELECT_AGGREGATE" and out.tables_mentioned == []


def test_classifier_reports_mentions(index):
    out = QueryClassifier(index).classify("List orders with customer names")
    assert out.tables_mentioned == ["customers", "orders"] and out.columns_mentioned == ["customers.name"]


def test_linker_resolves_tables_columns_and_values(index):
    out = SchemaLinker(index).link("orders per customer")
    assert out.resolved_tables == ["customers", "orders"]
    assert SchemaLinker(index).link("zzz").resolved_tables == []


def test_linker_context_includes_join_path_and_foreign_keys(index):
    out = SchemaLinker(index).link("customers who bought products")
    tables = {t.table for t in out.schema_context}
    assert {"customers", "orders", "order_items", "products"} <= tables
    orders = next(t for t in out.schema_context if t.table == "orders")
    assert "orders.customer_id -> customers.customer_id" in orders.foreign_keys


def test_linker_context_defaults_to_full_schema(index):
    out = SchemaLinker(index).link("zzz")
    assert len(out.schema_context) == 6

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
