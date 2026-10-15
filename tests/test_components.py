import pytest

from backend.components.example_retriever import ExampleRetriever
from backend.components.query_classifier import QueryClassifier
from backend.components.result_formatter import ResultFormatter
from backend.components.schema_linker import SchemaLinker
from backend.components.sql_executor import SqlExecutor
from backend.components.sql_generator import GenerationError, SqlGenerator
from backend.db import DatabaseError, QueryResult
from backend.embeddings import HashEmbedding
from backend.schema_index import SchemaIndex
from backend.schemas import ExampleIn, ExecutorOutput, GeneratorInput
from backend.store import AppStore
from backend.testing.inmemory_db import InMemoryDatabase


@pytest.fixture(scope="module")
def database():
    return InMemoryDatabase()


@pytest.fixture
def index(database, tmp_path):
    store = AppStore(tmp_path / "app.db").init()
    return SchemaIndex(store, database)


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
    store = AppStore(tmp_path / "app.db").init()
    store.create_example(ExampleIn(question="average price by category", sql="SELECT 1"))
    store.create_example(ExampleIn(question="failed payments list", sql="SELECT 2"))
    out = ExampleRetriever(store, tmp_path / "chroma", HashEmbedding()).retrieve("show failed payments", top_k=1)
    assert [e.question for e in out.examples] == ["failed payments list"]


def test_generator_matches_and_fails_closed():
    gen = SqlGenerator()
    assert "GROUP BY status" in gen.generate(GeneratorInput(nl_query="Count orders by status")).sql
    with pytest.raises(GenerationError):
        gen.generate(GeneratorInput(nl_query="what is the meaning of life"))


def test_executor_returns_rows_and_columns(database):
    out = SqlExecutor(database).execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY status")
    assert out.error is None and out.columns == ["status", "n"] and [r[0] for r in out.rows] == ["cancelled", "completed", "pending"]
    assert sum(r[1] for r in out.rows) == 5000 and out.row_count == 3 and not out.truncated


@pytest.mark.parametrize("sql,message", [
    ("DELETE FROM orders", "Only SELECT"),
    ("DROP TABLE orders", "Only SELECT"),
    ("UPDATE orders SET status = 'x'", "Only SELECT"),
    ("INSERT INTO customers (customer_id, name) VALUES (9999, 'x')", "Only SELECT"),
    ("EXEC sp_who", "Only SELECT"),
    ("SELECT * INTO copy_of_orders FROM orders", "SELECT INTO"),
    ("SELECT 1; DELETE FROM orders", "Exactly one"),
    ("SELEC nonsense (((", "Syntax error"),
])
def test_executor_refuses_anything_but_one_plain_query_and_the_data_is_untouched(database, sql, message):
    ex = SqlExecutor(database)
    out = ex.execute(sql)
    assert message in out.error and out.rows == [] and out.row_count == 0
    assert ex.execute("SELECT COUNT(*) FROM orders").rows == [[5000]]


def test_executor_surfaces_database_errors_instead_of_raising(database):
    out = SqlExecutor(database).execute("SELECT * FROM missing_table")
    assert out.error and "missing_table" in out.error and out.row_count == 0


def test_executor_reports_an_unreachable_database():
    class Down:
        def query(self, sql, max_rows, timeout_s):
            raise DatabaseError("Could not connect to Azure SQL (08001)")

    out = SqlExecutor(Down()).execute("SELECT 1")
    assert out.error == "Could not connect to Azure SQL (08001)" and out.rows == []


def test_executor_passes_the_limit_and_timeout_to_the_database():
    seen = {}

    class Spy:
        def query(self, sql, max_rows, timeout_s):
            seen.update(sql=sql, max_rows=max_rows, timeout_s=timeout_s)
            return QueryResult(["x"], [[1]], False)

    ex = SqlExecutor(Spy(), timeout_s=2.5, max_fetch_rows=50)
    assert ex.execute("SELECT 1 AS x", max_rows=10).rows == [[1]] and (seen["max_rows"], seen["timeout_s"]) == (10, 2.5)
    ex.execute("SELECT 1 AS x", max_rows=5000)
    assert seen["max_rows"] == 50, "the executor's own cap wins over a larger request"
    ex.execute("SELECT 1 AS x")
    assert seen["max_rows"] == 50


def test_formatter():
    out = ResultFormatter().format(ExecutorOutput(rows=[["a", 1]], columns=["k", "v"], row_count=1), "q")
    assert out.table == [{"k": "a", "v": 1}] and out.row_count == 1


def test_executor_truncates_to_max_rows(database):
    out = SqlExecutor(database).execute("SELECT order_id FROM orders", max_rows=10)
    assert out.row_count == 10 and out.truncated
    assert not SqlExecutor(database).execute("SELECT order_id FROM orders", max_rows=10000).truncated


def test_executor_timeout_on_runaway_query(database):
    ex = SqlExecutor(database, timeout_s=0.3)
    out = ex.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")
    assert out.error and "time limit" in out.error and out.latency_ms < 3000
