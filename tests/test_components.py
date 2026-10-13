import sqlite3
from pathlib import Path

import pytest

from backend.components.example_retriever import ExampleRetriever
from backend.components.query_classifier import QueryClassifier
from backend.components.result_formatter import ResultFormatter
from backend.components.schema_linker import SchemaLinker
from backend.components.sql_executor import SqlExecutor
from backend.components.sql_generator import GenerationError, SqlGenerator
from backend.schemas import ExecutorOutput, GeneratorInput

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


def test_retriever(db):
    out = ExampleRetriever(db).retrieve("anything", top_k=3)
    assert [e.question for e in out.examples] == ["q1"]


def test_retriever_missing_db(tmp_path):
    assert ExampleRetriever(tmp_path / "missing.db").retrieve("x").examples == []


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
