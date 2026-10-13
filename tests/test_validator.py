import pytest

from backend.components.sql_validator import SqlValidator, load_sqlite_schema
from backend.config import DB_PATH
from backend.schemas import Guardrails

SCHEMA = {
    "customers": {"customer_id", "name", "email", "city", "signup_date"},
    "orders": {"order_id", "customer_id", "order_date", "total_amount", "status"},
    "order_items": {"item_id", "order_id", "product_id", "quantity", "unit_price"},
    "products": {"product_id", "name", "category", "price"},
    "query_logs": {"id", "nl_query"},
}


def validator(**guardrail_kwargs):
    return SqlValidator(schema=SCHEMA, guardrails=Guardrails(**guardrail_kwargs))


def check(sql, **kw):
    return validator().validate(sql, **kw)


DESTRUCTIVE = [
    "DROP TABLE orders",
    "drop table orders;",
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO customers (name) VALUES ('x')",
    "REPLACE INTO customers (name) VALUES ('x')",
    "TRUNCATE TABLE orders",
    "ALTER TABLE orders ADD COLUMN x INT",
    "CREATE TABLE evil AS SELECT * FROM orders",
    "CREATE TABLE evil (id INT)",
]
STACKED_AND_HIDDEN = [
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM orders; DELETE FROM orders",
    "/* SELECT */ DROP TABLE orders",
    "-- SELECT\nDROP TABLE orders",
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "SELECT * FROM orders WHERE order_id IN (SELECT 1); UPDATE orders SET status='x'",
]
SQLITE_ESCAPES = [
    "PRAGMA writable_schema = 1",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE 'evil.db' AS e",
    "DETACH DATABASE e",
    "VACUUM INTO 'copy.db'",
    "SELECT load_extension('evil.dll')",
    "SELECT readfile('/etc/passwd')",
    "SELECT * FROM pragma_table_info('orders')",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM main.orders",
    "BEGIN TRANSACTION",
]
OUT_OF_SCHEMA = [
    ("SELECT * FROM secrets", "Unknown table"),
    ("SELECT * FROM query_logs", "not accessible"),
    ("SELECT nope FROM orders", "Unknown column"),
    ("SELECT o.nope FROM orders o", "Unknown column"),
    ("SELECT x.status FROM orders o", "Unknown table alias"),
]


@pytest.mark.parametrize("sql", DESTRUCTIVE + STACKED_AND_HIDDEN + SQLITE_ESCAPES)
def test_blocked(sql):
    out = check(sql)
    assert not out.valid and out.errors


@pytest.mark.parametrize("sql,fragment", OUT_OF_SCHEMA)
def test_schema_check(sql, fragment):
    out = check(sql)
    assert not out.valid and any(fragment in e for e in out.errors)


def test_syntax_error():
    out = check("SELEC * FROM orders")
    assert not out.valid and out.errors


def test_garbage_and_empty():
    assert not check("").valid
    assert not check("what is the meaning of life").valid


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders",
    "SELECT status, COUNT(*) FROM orders GROUP BY status",
    "SELECT c.name, SUM(o.total_amount) AS spent FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.name ORDER BY spent DESC LIMIT 5",
    "SELECT strftime('%Y-%m', order_date) AS month, SUM(total_amount) AS revenue FROM orders GROUP BY month ORDER BY month",
    "SELECT * FROM orders WHERE order_date >= date('now', '-30 days')",
    "SELECT * FROM orders WHERE status = 'drop; delete'",
    "WITH top AS (SELECT customer_id, SUM(total_amount) AS s FROM orders GROUP BY customer_id) SELECT c.name, top.s FROM top JOIN customers c ON c.customer_id = top.customer_id",
    "SELECT name FROM customers UNION SELECT name FROM products",
    "SELECT * FROM (SELECT status, COUNT(*) AS n FROM orders GROUP BY status) t WHERE t.n > 1",
    "SELECT COUNT(*) FROM orders;",
    "SELECT * FROM orders -- trailing comment",
    "SELECT o.* FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
])
def test_valid_queries_pass(sql):
    out = check(sql)
    assert out.valid, out.errors


def test_limit_added_and_comments_stripped():
    assert check("SELECT * FROM orders;", max_rows=5).sanitized_sql == "SELECT * FROM orders LIMIT 5"
    assert "--" not in check("SELECT * FROM orders -- hi").sanitized_sql


def test_limit_capped_and_respected():
    assert check("SELECT * FROM orders LIMIT 10", max_rows=100).sanitized_sql.endswith("LIMIT 10")
    assert check("SELECT * FROM orders LIMIT 5000", max_rows=100).sanitized_sql.endswith("LIMIT 100")
    assert check("SELECT * FROM orders LIMIT 1 + 5000", max_rows=100).sanitized_sql.endswith("LIMIT 100")


def test_limit_on_union_and_column_named_limit_like():
    assert check("SELECT name FROM customers UNION SELECT name FROM products", max_rows=7).sanitized_sql.endswith("LIMIT 7")
    assert check("SELECT status AS limit_x FROM orders", max_rows=3).sanitized_sql.endswith("LIMIT 3")


def test_guardrail_max_rows_wins():
    v = validator(max_rows=20)
    assert v.validate("SELECT * FROM orders", max_rows=500).sanitized_sql.endswith("LIMIT 20")


def test_table_prefix_scope():
    v = validator(allowed_table_prefixes=["order"])
    assert v.validate("SELECT * FROM orders").valid
    out = v.validate("SELECT * FROM customers")
    assert not out.valid and "outside the allowed scope" in out.errors[0]


def test_toggling_an_operation_allows_it_but_not_others():
    v = validator(blocked_operations=["DROP", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE"])
    assert v.validate("DELETE FROM orders WHERE status = 'x'").valid
    assert not v.validate("DROP TABLE orders").valid
    assert not v.validate("PRAGMA writable_schema=1").valid
    assert not v.validate("SELECT 1; DELETE FROM orders").valid


def test_dynamic_guardrails_provider():
    state = {"g": Guardrails()}
    v = SqlValidator(schema=SCHEMA, guardrails=lambda: state["g"])
    assert not v.validate("DELETE FROM orders").valid
    state["g"] = Guardrails(blocked_operations=[])
    assert v.validate("DELETE FROM orders").valid


@pytest.mark.skipif(not DB_PATH.exists(), reason="run data/seed_db.py first")
def test_real_schema_loads_and_validates():
    schema = load_sqlite_schema()
    assert {"customers", "orders", "order_items", "payments", "products", "reviews"} <= set(schema)
    v = SqlValidator(schema=schema)
    assert v.validate("SELECT status, COUNT(*) FROM orders GROUP BY status").valid
    assert not v.validate("SELECT * FROM query_logs").valid
