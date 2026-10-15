import pytest

from backend.components.sql_validator import SqlValidator
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
    "ALTER TABLE orders ADD hacked NVARCHAR(10)",
    "CREATE TABLE evil (id INT)",
    "CREATE VIEW v AS SELECT * FROM customers",
    "CREATE INDEX i ON orders(status)",
    "MERGE orders AS t USING customers AS s ON t.customer_id = s.customer_id WHEN MATCHED THEN DELETE;",
]
STACKED_AND_HIDDEN = [
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM orders; DELETE FROM orders",
    "/* SELECT */ DROP TABLE orders",
    "-- SELECT\nDROP TABLE orders",
    "WITH d AS (SELECT * FROM orders) DELETE FROM d",
    "WITH d AS (SELECT * FROM orders) UPDATE d SET status = 'x'",
    "SELECT * FROM orders WHERE order_id IN (SELECT 1); DROP TABLE customers",
    "SELECT 1 GO DROP TABLE orders",
]
TSQL_ESCAPES = [
    "EXEC sp_who",
    "EXEC xp_cmdshell 'dir'",
    "EXECUTE ('DROP TABLE orders')",
    "SELECT * INTO copy_of_orders FROM orders",
    "SELECT order_id INTO #t FROM orders",
    "SELECT @@version",
    "SELECT @@servername",
    "DECLARE @x INT = 1",
    "SET NOCOUNT ON",
    "USE master",
    "WAITFOR DELAY '00:00:10'",
    "BEGIN TRANSACTION",
    "COMMIT",
    "GRANT SELECT ON orders TO public",
    "DBCC CHECKDB",
    "BACKUP DATABASE x TO DISK = 'y'",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')",
    "SELECT * FROM OPENQUERY(linked, 'SELECT 1')",
    "SELECT DB_NAME()",
    "SELECT SUSER_SNAME()",
    "SELECT * FROM sys.tables",
    "SELECT * FROM sys.sql_logins",
    "SELECT * FROM INFORMATION_SCHEMA.TABLES",
    "SELECT * FROM master..sysobjects",
    "SELECT * FROM otherdb.dbo.orders",
    "SELECT * FROM other.orders",
    "SELECT * FROM #temp",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'evil.db' AS e",
    "SELECT load_extension('evil.dll')",
    "SELECT readfile('/etc/passwd')",
    "SELECT writefile('/tmp/x', 'data')",
]
OUT_OF_SCHEMA = [
    ("SELECT * FROM secrets", "Unknown table"),
    ("SELECT * FROM query_logs", "not accessible"),
    ("SELECT nope FROM orders", "Unknown column"),
    ("SELECT o.nope FROM orders o", "Unknown column"),
    ("SELECT x.status FROM orders o", "Unknown table alias"),
]
SQLITE_ISMS = [
    ("SELECT * FROM orders LIMIT 5", "T-SQL has no LIMIT"),
    ("SELECT * FROM orders WHERE order_date >= date('now', '-30 days')", "'date' is not a recognized"),
    ("SELECT strftime('%Y-%m', order_date) FROM orders", "FORMAT"),
    ("SELECT julianday(order_date) FROM orders", "DATEDIFF"),
    ("SELECT ifnull(city, 'x') FROM customers", "ISNULL"),
    ("SELECT substr(name, 1, 3) FROM customers", "SUBSTRING"),
]


@pytest.mark.parametrize("sql", DESTRUCTIVE + STACKED_AND_HIDDEN + TSQL_ESCAPES)
def test_blocked(sql):
    out = check(sql)
    assert not out.valid and out.errors


@pytest.mark.parametrize("sql,fragment", OUT_OF_SCHEMA)
def test_schema_check(sql, fragment):
    out = check(sql)
    assert not out.valid and any(fragment in e for e in out.errors)


@pytest.mark.parametrize("sql,fragment", SQLITE_ISMS)
def test_sqlite_constructs_are_rejected_with_a_tsql_hint(sql, fragment):
    out = check(sql)
    assert not out.valid and any(fragment in e for e in out.errors), out.errors


@pytest.mark.parametrize("sql,fragment", [
    ("SELECT * INTO copy FROM orders", "SELECT INTO"),
    ("EXEC sp_who", "Only SELECT"),
    ("SELECT @@version", "Variables"),
    ("SELECT * FROM sys.tables", "Schema-qualified"),
    ("SELECT * FROM OPENROWSET('a', 'b', 'c')", "not allowed"),
    ("SELECT DB_NAME()", "DB_NAME"),
])
def test_specific_tsql_messages(sql, fragment):
    out = check(sql)
    assert not out.valid and any(fragment in e for e in out.errors), out.errors


def test_syntax_error():
    out = check("SELEC * FROM orders")
    assert not out.valid and out.errors


def test_garbage_and_empty():
    assert not check("").valid
    assert not check("what is the meaning of life").valid


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders",
    "SELECT status, COUNT(*) FROM orders GROUP BY status",
    "SELECT TOP 5 c.name, SUM(o.total_amount) AS spent FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.name ORDER BY spent DESC",
    "SELECT FORMAT(order_date, 'yyyy-MM') AS month, SUM(total_amount) AS revenue FROM orders GROUP BY FORMAT(order_date, 'yyyy-MM') ORDER BY month",
    "SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -30, GETDATE())",
    "SELECT * FROM orders WHERE order_date >= CAST(DATEADD(day, -30, GETDATE()) AS DATE)",
    "SELECT YEAR(signup_date) AS y, COUNT(*) AS n FROM customers GROUP BY YEAR(signup_date)",
    "SELECT AVG(CAST(price AS FLOAT)) AS avg_price FROM products",
    "SELECT AVG(DATEDIFF(day, order_date, order_date)) FROM orders",
    "SELECT * FROM orders WHERE status = 'drop; delete'",
    "WITH top_c AS (SELECT customer_id, SUM(total_amount) AS s FROM orders GROUP BY customer_id) SELECT c.name, top_c.s FROM top_c JOIN customers c ON c.customer_id = top_c.customer_id",
    "SELECT name FROM customers UNION SELECT name FROM products",
    "SELECT * FROM (SELECT status, COUNT(*) AS n FROM orders GROUP BY status) t WHERE t.n > 1",
    "SELECT COUNT(*) FROM orders;",
    "SELECT * FROM orders -- trailing comment",
    "SELECT o.* FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
    "SELECT * FROM dbo.orders",
    "SELECT o.order_id FROM dbo.orders AS o",
    "SELECT [order_id], [status] FROM [orders]",
    "SELECT * FROM orders WITH (NOLOCK)",
    "SELECT name FROM customers ORDER BY name OFFSET 5 ROWS FETCH NEXT 10 ROWS ONLY",
    "SELECT DISTINCT TOP 10 city FROM customers ORDER BY city",
    "SELECT status AS status, COUNT(*) FROM orders WHERE status = 'pending' GROUP BY status",
])
def test_valid_queries_pass(sql):
    out = check(sql)
    assert out.valid, out.errors


def test_top_is_added_and_comments_stripped():
    assert check("SELECT * FROM orders;", max_rows=5).sanitized_sql == "SELECT TOP 5 * FROM orders"
    assert "--" not in check("SELECT * FROM orders -- hi").sanitized_sql


def test_existing_top_is_respected_or_capped():
    assert check("SELECT TOP 10 * FROM orders", max_rows=100).sanitized_sql == "SELECT TOP 10 * FROM orders"
    assert check("SELECT TOP 5000 * FROM orders", max_rows=100).sanitized_sql == "SELECT TOP 100 * FROM orders"
    assert check("SELECT TOP (5000) * FROM orders", max_rows=100).sanitized_sql == "SELECT TOP 100 * FROM orders"


def test_offset_fetch_is_capped_and_offset_alone_gets_a_fetch():
    assert check("SELECT name FROM customers ORDER BY name OFFSET 5 ROWS FETCH NEXT 5000 ROWS ONLY", max_rows=20).sanitized_sql.endswith("FETCH FIRST 20 ROWS ONLY")
    assert check("SELECT name FROM customers ORDER BY name OFFSET 5 ROWS", max_rows=20).sanitized_sql.endswith("OFFSET 5 ROWS FETCH FIRST 20 ROWS ONLY")


def test_top_on_union_and_cte_and_alias_named_like_limit():
    assert check("SELECT name FROM customers UNION SELECT name FROM products", max_rows=7).sanitized_sql.startswith("SELECT TOP 7 * FROM (")
    assert check("WITH c AS (SELECT * FROM orders) SELECT * FROM c", max_rows=7).sanitized_sql.endswith("SELECT TOP 7 * FROM c")
    assert check("SELECT status AS limit_x FROM orders", max_rows=3).sanitized_sql == "SELECT TOP 3 status AS limit_x FROM orders"


def test_the_output_stays_valid_tsql_for_everything_that_passes():
    import sqlglot
    for sql in ["SELECT status, COUNT(*) FROM orders GROUP BY status", "SELECT name FROM customers UNION SELECT name FROM products",
                "SELECT TOP 5 * FROM orders ORDER BY order_id", "SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -30, GETDATE())"]:
        out = check(sql).sanitized_sql
        assert sqlglot.parse_one(out, read="tsql").sql(dialect="tsql") == out
        assert "LIMIT" not in out.upper()


def test_guardrail_max_rows_wins():
    v = validator(max_rows=20)
    assert v.validate("SELECT * FROM orders", max_rows=500).sanitized_sql == "SELECT TOP 20 * FROM orders"


@pytest.mark.parametrize("sql", [
    "SELECT FORMAT(order_date, 'yyyy-MM') AS month, COUNT(*) FROM orders GROUP BY month",
    "SELECT status AS s, COUNT(*) FROM orders WHERE s = 'pending' GROUP BY status",
    "SELECT SUM(total_amount) AS total FROM orders GROUP BY status HAVING total > 5",
])
def test_select_alias_in_where_group_by_or_having_is_caught_before_the_database(sql):
    out = check(sql)
    assert not out.valid and any("T-SQL does not allow a select alias" in e for e in out.errors), out.errors


def test_alias_rule_does_not_flag_order_by_real_columns_or_derived_tables():
    assert check("SELECT status AS n FROM orders ORDER BY n").valid
    assert check("SELECT total_amount AS total_amount FROM orders WHERE total_amount > 5").valid
    assert check("SELECT * FROM (SELECT FORMAT(order_date, 'yyyy-MM') AS month FROM orders) t WHERE month = '2026-01'").valid


def test_table_prefix_scope():
    v = validator(allowed_table_prefixes=["order"])
    assert v.validate("SELECT * FROM orders").valid
    out = v.validate("SELECT * FROM customers")
    assert not out.valid and "outside the allowed scope" in out.errors[0]


def test_toggling_an_operation_allows_it_but_not_others():
    v = validator(blocked_operations=["DROP", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE"])
    assert v.validate("DELETE FROM orders WHERE status = 'x'").valid
    assert not v.validate("DROP TABLE orders").valid
    assert not v.validate("EXEC sp_who").valid
    assert not v.validate("SELECT 1; DELETE FROM orders").valid


def test_dynamic_guardrails_provider():
    state = {"g": Guardrails()}
    v = SqlValidator(schema=SCHEMA, guardrails=lambda: state["g"])
    assert not v.validate("DELETE FROM orders").valid
    state["g"] = Guardrails(blocked_operations=[])
    assert v.validate("DELETE FROM orders").valid


def test_schema_can_be_a_provider_so_changes_are_picked_up():
    state = {"schema": {"orders": {"order_id"}}}
    v = SqlValidator(schema=lambda: state["schema"])
    assert not v.validate("SELECT status FROM orders").valid
    state["schema"] = {"orders": {"order_id", "status"}}
    assert v.validate("SELECT status FROM orders").valid


def test_identifier_matching_is_case_insensitive_like_sql_server():
    assert check("SELECT ORDER_ID, Status FROM Orders").valid


def test_validates_against_the_real_demo_schema():
    from backend.db import AnalyticsDatabase  # noqa: F401  (protocol import keeps the dependency direction honest)
    from backend.testing.inmemory_db import InMemoryDatabase
    schema = {t: {c.name.lower() for c in cols} for t, cols in InMemoryDatabase().load_schema().tables.items()}
    v = SqlValidator(schema=schema)
    assert v.validate("SELECT status, COUNT(*) FROM orders GROUP BY status").valid
    assert not v.validate("SELECT * FROM query_logs").valid
