import datetime
import decimal
import uuid

import pytest

from backend.db import AzureSqlDatabase, DatabaseError, QueryTimeout, normalize_value, quote_ident

SECRET = "Pwd=very-secret-password"
CONN = f"Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:x.database.windows.net,1433;Database=d;Uid=u;{SECRET};"


class OdbcError(Exception):
    pass


class StubCursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql):
        self.conn.executed.append(sql)
        if self.conn.fail:
            raise self.conn.fail
        columns, self._rows = self.conn.responder(sql)
        self.description = [(c,) for c in columns]

    def fetchmany(self, n):
        return self._rows[:n]

    def fetchall(self):
        return self._rows


class StubConn:
    def __init__(self, responder=lambda sql: (["x"], [(1,)]), fail=None):
        self.responder, self.fail, self.executed = responder, fail, []
        self.timeout, self.rolled_back, self.closed = None, False, False

    def cursor(self):
        return StubCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def db_with(conn, **kwargs):
    calls = []

    def connect(cs, timeout):
        calls.append((cs, timeout))
        return conn

    database = AzureSqlDatabase(CONN, connect=connect, **kwargs)
    database.calls = calls
    return database


def test_query_returns_normalised_rows_and_truncation():
    conn = StubConn(lambda sql: (["id", "amount", "day"], [(1, decimal.Decimal("10.50"), datetime.date(2026, 1, 2)),
                                                        (2, decimal.Decimal("3"), datetime.date(2026, 1, 3)),
                                                        (3, decimal.Decimal("1"), datetime.date(2026, 1, 4))]))
    result = db_with(conn).query("SELECT 1", 2, 5)
    assert result.columns == ["id", "amount", "day"] and result.truncated
    assert result.rows == [[1, 10.5, "2026-01-02"], [2, 3, "2026-01-03"]]


def test_unnamed_columns_get_a_name_because_sql_server_returns_an_empty_one():
    conn = StubConn(lambda sql: (["", "total", ""], [(1, 2, 3)]))
    result = db_with(conn).query("SELECT COUNT(*), 2 AS total, 3", 5, 5)
    assert result.columns == ["column_1", "total", "column_3"] and result.rows == [[1, 2, 3]]


def test_query_not_truncated_when_rows_fit():
    assert not db_with(StubConn(lambda sql: (["x"], [(1,), (2,)]))).query("SELECT 1", 2, 5).truncated


def test_query_sets_timeout_rolls_back_and_closes():
    conn = StubConn()
    database = db_with(conn, login_timeout_s=7)
    database.query("SELECT 1", 10, 2.2)
    assert conn.timeout == 3 and conn.rolled_back and conn.closed
    assert database.calls == [(CONN, 7)]


def test_failed_query_still_rolls_back_and_closes():
    conn = StubConn(fail=OdbcError("42S02", "Invalid object name 'nope'."))
    with pytest.raises(DatabaseError, match="Invalid object name"):
        db_with(conn).query("SELECT * FROM nope", 10, 5)
    assert conn.rolled_back and conn.closed


@pytest.mark.parametrize("state", ["HYT00", "HYT01"])
def test_timeouts_map_to_query_timeout(state):
    with pytest.raises(QueryTimeout, match="3s time limit"):
        db_with(StubConn(fail=OdbcError(state, "Timeout expired"))).query("SELECT 1", 10, 3)


def test_missing_connection_string_is_a_clear_error():
    with pytest.raises(DatabaseError, match="AZURE_SQL_CONNECTION_STRING is not set"):
        AzureSqlDatabase("").query("SELECT 1", 1, 1)


def test_connection_failures_do_not_leak_the_connection_string():
    def connect(cs, timeout):
        raise OdbcError("08001", f"Cannot open server using {cs}")

    with pytest.raises(DatabaseError) as e:
        AzureSqlDatabase(CONN, connect=connect).ping()
    assert "very-secret-password" not in str(e.value) and "08001" in str(e.value)


def test_load_schema_assembles_tables_columns_keys_and_relationships():
    def respond(sql):
        if "FROM sys.tables t JOIN sys.schemas s" in sql and "c.name" not in sql and "ty.name" not in sql and "foreign" not in sql:
            return (["name"], [("customers",), ("orders",)])
        if "ty.name" in sql:
            return (["t", "c", "ty"], [("customers", "customer_id", "int"), ("customers", "city", "nvarchar"),
                                       ("orders", "order_id", "int"), ("orders", "customer_id", "int"), ("ghost", "x", "int")])
        if "is_primary_key" in sql:
            return (["t", "c"], [("customers", "customer_id"), ("orders", "order_id")])
        if "foreign_key_columns" in sql:
            return (["t", "c", "rt", "rc"], [("orders", "customer_id", "customers", "customer_id")])
        raise AssertionError(sql)

    schema = db_with(StubConn(respond)).load_schema()
    assert list(schema.tables) == ["customers", "orders"]
    assert [(c.name, c.type) for c in schema.tables["customers"]] == [("customer_id", "INT"), ("city", "NVARCHAR")]
    assert schema.primary_keys == {"customers": {"customer_id"}, "orders": {"order_id"}}
    assert schema.foreign_keys == [("orders", "customer_id", "customers", "customer_id")]


def test_schema_queries_are_scoped_to_dbo_and_creation_order():
    conn = StubConn(lambda sql: ([], []))
    db_with(conn).load_schema()
    assert all("'dbo'" in sql for sql in conn.executed) and "ORDER BY t.object_id" in conn.executed[0]


def test_sample_values_quotes_identifiers_and_limits_rows():
    conn = StubConn(lambda sql: (["v"], [("Mumbai",), ("Delhi",)]))
    values = db_with(conn).sample_values("orders", "sta]tus", 51)
    assert values == ["Mumbai", "Delhi"]
    assert conn.executed[0] == "SELECT DISTINCT TOP (51) [sta]]tus] FROM dbo.[orders] WHERE [sta]]tus] IS NOT NULL"


def test_fingerprint_counts_and_checksums_each_table():
    def respond(sql):
        if "sys.tables" in sql and "ty.name" not in sql and "foreign" not in sql and "is_primary_key" not in sql:
            return (["name"], [("a",), ("b",)])
        if "COUNT_BIG" in sql:
            return (["c", "k"], [(5 if "[a]" in sql else 7, 123)])
        return ([], [])

    assert db_with(StubConn(respond)).fingerprint() == {"a": "5:123", "b": "7:123"}


def test_read_only_status_reports_the_privileges_found():
    safe = db_with(StubConn(lambda sql: (["x"] * 8, [(0,) * 8]))).read_only_status()
    assert safe.read_only and "no write permissions" in safe.detail
    risky = db_with(StubConn(lambda sql: (["x"] * 8, [(1, 0, 0, 1, 0, 0, 0, 0)]))).read_only_status()
    assert not risky.read_only and "db_owner" in risky.detail and "INSERT permission" in risky.detail and "UPDATE" not in risky.detail


def test_ping_failure_is_reported():
    with pytest.raises(DatabaseError):
        db_with(StubConn(fail=OdbcError("08S01", "link failure"))).ping()


@pytest.mark.parametrize("value,expected", [
    (None, None), (True, True), (5, 5), ("x", "x"), (1.5, 1.5), (float("nan"), None), (float("inf"), None),
    (decimal.Decimal("5"), 5), (decimal.Decimal("5.25"), 5.25), (decimal.Decimal("NaN"), None),
    (datetime.date(2026, 1, 2), "2026-01-02"), (datetime.datetime(2026, 1, 2, 3, 4, 5), "2026-01-02 03:04:05"),
    (datetime.time(3, 4), "03:04:00"), (b"\x00\xff", "0x00ff"), (bytearray(b"\x01"), "0x01"),
    (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
])
def test_normalize_value(value, expected):
    assert normalize_value(value) == expected


def test_quote_ident_escapes_closing_brackets():
    assert quote_ident("orders") == "[orders]" and quote_ident("a]b") == "[a]]b]" and quote_ident("x]; DROP") == "[x]]; DROP]"


def test_importing_the_backend_does_not_require_pyodbc():
    import subprocess
    import sys
    code = "import sys; sys.modules['pyodbc'] = None; import backend.db; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "ok", out.stderr
