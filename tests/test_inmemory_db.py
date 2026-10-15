from datetime import date

import pytest

from backend import demo_data
from backend.db import DatabaseError, QueryTimeout
from backend.testing.inmemory_db import InMemoryDatabase


@pytest.fixture(scope="module")
def db():
    return InMemoryDatabase(today=date.today())


def rows(db, sql, n=10000, timeout=5):
    return db.query(sql, n, timeout).rows


def test_loads_the_demo_dataset(db):
    for table, count in demo_data.ROW_COUNTS.items():
        assert rows(db, f"SELECT COUNT(*) FROM {table}") == [[count]]


def test_result_shape_and_truncation(db):
    result = db.query("SELECT customer_id, name FROM customers ORDER BY customer_id", 10, 5)
    assert result.columns == ["customer_id", "name"] and len(result.rows) == 10 and result.truncated
    assert not db.query("SELECT TOP 5 name FROM customers", 10, 5).truncated
    assert db.query("SELECT TOP 5 name FROM customers", 10, 5).rows.__len__() == 5


def test_top_offset_fetch_and_dbo_prefix(db):
    assert len(rows(db, "SELECT TOP 3 name FROM dbo.customers ORDER BY customer_id")) == 3
    assert len(rows(db, "SELECT name FROM customers ORDER BY customer_id OFFSET 5 ROWS FETCH NEXT 4 ROWS ONLY")) == 4


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers LIMIT 3",
    "SELECT name FROM customers ORDER BY name LIMIT 1",
])
def test_limit_is_rejected_like_sql_server(db, sql):
    with pytest.raises(DatabaseError, match="near 'LIMIT'"):
        db.query(sql, 10, 5)


@pytest.mark.parametrize("sql,func", [
    ("SELECT strftime('%Y', order_date) FROM orders", "strftime"),
    ("SELECT * FROM orders WHERE order_date >= date('now', '-30 days')", "date"),
    ("SELECT julianday(order_date) FROM orders", "julianday"),
    ("SELECT ifnull(city, 'x') FROM customers", "ifnull"),
])
def test_sqlite_only_functions_are_rejected(db, sql, func):
    with pytest.raises(DatabaseError, match=f"'{func}' is not a recognized built-in function"):
        db.query(sql, 10, 5)


@pytest.mark.parametrize("clause", [
    "GROUP BY month",
    "GROUP BY month HAVING COUNT(*) > 1",
    "WHERE month = '2026-01' GROUP BY FORMAT(order_date, 'yyyy-MM')",
])
def test_select_alias_in_where_group_having_is_invalid(db, clause):
    with pytest.raises(DatabaseError, match="Invalid column name 'month'"):
        db.query(f"SELECT FORMAT(order_date, 'yyyy-MM') AS month, COUNT(*) AS n FROM orders {clause}", 10, 5)


def test_alias_in_order_by_and_real_column_names_are_fine(db):
    sql = "SELECT FORMAT(order_date, 'yyyy-MM') AS month, COUNT(*) AS n FROM orders GROUP BY FORMAT(order_date, 'yyyy-MM') ORDER BY month"
    result = db.query(sql, 100, 5)
    assert result.rows == sorted(result.rows)
    assert rows(db, "SELECT status AS status FROM orders WHERE status = 'pending' GROUP BY status") == [["pending"]]


def test_text_comparison_is_case_insensitive_like_the_default_collation(db):
    assert rows(db, "SELECT COUNT(*) FROM customers WHERE city = 'mumbai'") == rows(db, "SELECT COUNT(*) FROM customers WHERE city = 'MUMBAI'")
    assert rows(db, "SELECT COUNT(*) FROM customers WHERE city = 'mumbai'")[0][0] > 0


def test_average_of_integers_truncates_but_floats_do_not(db):
    truncated = rows(db, "SELECT AVG(rating) FROM reviews")[0][0]
    exact = rows(db, "SELECT AVG(CAST(rating AS FLOAT)) FROM reviews")[0][0]
    assert isinstance(truncated, int) and truncated == int(exact) and exact != truncated
    assert isinstance(rows(db, "SELECT AVG(price) FROM products")[0][0], float)
    assert rows(db, "SELECT AVG(rating) FROM reviews WHERE rating > 99") == [[None]]


def test_date_functions_work_in_tsql_form(db):
    assert rows(db, "SELECT COUNT(*) FROM orders WHERE order_date >= DATEADD(DAY, -30, GETDATE())")[0][0] > 0
    years = rows(db, "SELECT YEAR(signup_date) AS y, COUNT(*) AS n FROM customers GROUP BY YEAR(signup_date) ORDER BY y")
    assert sum(n for _, n in years) == 1000 and all(isinstance(y, int) for y, _ in years)
    assert 1 <= rows(db, "SELECT AVG(DATEDIFF(day, order_date, shipping_date)) FROM orders")[0][0] <= 7
    assert rows(db, "SELECT MONTH(order_date) FROM orders WHERE order_id = 1")[0][0] in range(1, 13)


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO customers (customer_id, name) VALUES (9999, 'x')",
    "DROP TABLE orders",
    "CREATE TABLE evil (id INT)",
    "SELECT * INTO copy_of_orders FROM orders",
    "EXEC sp_who",
    "SELECT 1; DELETE FROM orders",
])
def test_the_login_is_read_only(db, sql):
    with pytest.raises(DatabaseError, match="permission"):
        db.query(sql, 10, 5)
    assert rows(db, "SELECT COUNT(*) FROM orders") == [[5000]]


def test_syntax_errors_surface_as_database_errors(db):
    with pytest.raises(DatabaseError):
        db.query("SELEC nonsense (((", 10, 5)
    with pytest.raises(DatabaseError):
        db.query("SELECT nope FROM orders", 10, 5)
    with pytest.raises(DatabaseError):
        db.query("SELECT * FROM missing_table", 10, 5)


def test_runaway_queries_time_out(db):
    with pytest.raises(QueryTimeout, match="0.3s"):
        db.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c", 10, 0.3)
    assert rows(db, "SELECT 1") == [[1]], "the connection stays usable after a timeout"


def test_schema_matches_the_dataset_spec(db):
    schema = db.load_schema()
    assert list(schema.tables) == list(demo_data.TABLE_NAMES)
    assert [c.name for c in schema.tables["orders"]] == ["order_id", "customer_id", "order_date", "total_amount", "status", "shipping_date"]
    assert {c.name: c.type for c in schema.tables["customers"]}["city"] == "NVARCHAR"
    assert schema.primary_keys["order_items"] == {"item_id"}
    assert ("orders", "customer_id", "customers", "customer_id") in schema.foreign_keys
    assert len(schema.foreign_keys) == 6


def test_sample_values_are_distinct_and_non_null(db):
    values = db.sample_values("orders", "status", 10)
    assert sorted(values) == sorted(demo_data.ORDER_STATUSES)
    assert len(db.sample_values("customers", "name", 5)) == 5


def test_fingerprint_is_stable_and_detects_different_data(db):
    assert db.fingerprint() == db.fingerprint()
    other = InMemoryDatabase(demo_data.generate(seed=7, today=date.today()))
    assert other.fingerprint() != db.fingerprint()
    assert set(db.fingerprint()) == set(demo_data.TABLE_NAMES)


def test_read_only_status_and_ping(db):
    assert db.read_only_status().read_only
    db.ping()


def test_same_data_gives_same_results_across_instances():
    one = InMemoryDatabase(demo_data.generate(today=date(2026, 1, 1)))
    two = InMemoryDatabase(demo_data.generate(today=date(2026, 1, 1)))
    sql = "SELECT TOP 20 name, SUM(total_amount) AS s FROM customers c JOIN orders o ON o.customer_id = c.customer_id GROUP BY name ORDER BY s DESC, name"
    assert one.query(sql, 100, 5).rows == two.query(sql, 100, 5).rows
