"""Analytics database access. The product runs on Azure SQL; everything else talks to this small interface so the
pipeline can be tested without a server (see backend/testing/inmemory_db.py)."""
import datetime
import decimal
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


class DatabaseError(Exception):
    pass


class QueryTimeout(DatabaseError):
    pass


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str


@dataclass
class SchemaInfo:
    tables: dict[str, list[ColumnInfo]] = field(default_factory=dict)
    primary_keys: dict[str, set[str]] = field(default_factory=dict)
    foreign_keys: list[tuple[str, str, str, str]] = field(default_factory=list)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


@dataclass
class ReadOnlyStatus:
    read_only: bool
    detail: str


class AnalyticsDatabase(Protocol):
    dialect: str

    def query(self, sql: str, max_rows: int, timeout_s: float) -> QueryResult: ...

    def load_schema(self) -> SchemaInfo: ...

    def sample_values(self, table: str, column: str, limit: int) -> list[Any]: ...

    def fingerprint(self) -> dict[str, str]: ...

    def read_only_status(self) -> ReadOnlyStatus: ...

    def ping(self) -> None: ...


def quote_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def normalize_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() and value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


_TIMEOUT_STATES = {"HYT00", "HYT01"}
_SCHEMA = "dbo"
_TABLES_SQL = ("SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
               f"WHERE s.name = '{_SCHEMA}' ORDER BY t.object_id")
_COLUMNS_SQL = ("SELECT t.name, c.name, ty.name FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id "
                "JOIN sys.schemas s ON s.schema_id = t.schema_id JOIN sys.types ty ON ty.user_type_id = c.user_type_id "
                f"WHERE s.name = '{_SCHEMA}' ORDER BY t.object_id, c.column_id")
_PK_SQL = ("SELECT t.name, c.name FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
           "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id JOIN sys.tables t ON t.object_id = i.object_id "
           f"JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE i.is_primary_key = 1 AND s.name = '{_SCHEMA}'")
_FK_SQL = ("SELECT tp.name, cp.name, tr.name, cr.name FROM sys.foreign_key_columns fkc "
           "JOIN sys.tables tp ON tp.object_id = fkc.parent_object_id JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id "
           "JOIN sys.tables tr ON tr.object_id = fkc.referenced_object_id JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id AND cr.column_id = fkc.referenced_column_id "
           f"JOIN sys.schemas s ON s.schema_id = tp.schema_id WHERE s.name = '{_SCHEMA}'")
_WRITE_PERMISSIONS = ["INSERT", "UPDATE", "DELETE", "CREATE TABLE", "ALTER"]
_READ_ONLY_SQL = ("SELECT COALESCE(IS_MEMBER('db_owner'), 0), COALESCE(IS_MEMBER('db_datawriter'), 0), COALESCE(IS_MEMBER('db_ddladmin'), 0), "
                  + ", ".join(f"COALESCE(HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', '{p}'), 0)" for p in _WRITE_PERMISSIONS))
_READ_ONLY_LABELS = ["db_owner", "db_datawriter", "db_ddladmin"] + [f"{p} permission" for p in _WRITE_PERMISSIONS]


class AzureSqlDatabase:
    dialect = "tsql"

    def __init__(self, connection_string: str, login_timeout_s: int = 10, connect: Optional[Callable] = None):
        self._connection_string = connection_string
        self._login_timeout_s = login_timeout_s
        self._connect_fn = connect

    def _connect(self):
        if not self._connection_string:
            raise DatabaseError("AZURE_SQL_CONNECTION_STRING is not set")
        connect = self._connect_fn
        if connect is None:
            try:
                import pyodbc
            except ImportError as e:
                raise DatabaseError("pyodbc is not installed or the ODBC driver library is missing") from e
            connect = pyodbc.connect
        try:
            return connect(self._connection_string, timeout=self._login_timeout_s)
        except Exception as e:
            raise DatabaseError(f"Could not connect to Azure SQL ({self._state(e) or type(e).__name__})") from e

    @staticmethod
    def _state(exc: Exception) -> str:
        return str(exc.args[0]) if exc.args and isinstance(exc.args[0], str) and len(exc.args[0]) == 5 else ""

    def _run(self, sql: str, fetch: Callable, timeout_s: float):
        conn = self._connect()
        try:
            conn.timeout = max(1, int(math.ceil(timeout_s)))
            cur = conn.cursor()
            cur.execute(sql)
            return fetch(cur)
        except DatabaseError:
            raise
        except Exception as e:
            if self._state(e) in _TIMEOUT_STATES:
                raise QueryTimeout(f"Query exceeded the {timeout_s:g}s time limit") from e
            raise DatabaseError(str(e)) from e
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()

    def query(self, sql: str, max_rows: int, timeout_s: float) -> QueryResult:
        def fetch(cur):
            fetched = cur.fetchmany(max_rows + 1)
            columns = [d[0] for d in cur.description] if cur.description else []
            return QueryResult(columns, [[normalize_value(v) for v in row] for row in fetched[:max_rows]], len(fetched) > max_rows)
        return self._run(sql, fetch, timeout_s)

    def _all(self, sql: str, timeout_s: float = 30) -> list[tuple]:
        return self._run(sql, lambda cur: [tuple(r) for r in cur.fetchall()], timeout_s)

    def load_schema(self) -> SchemaInfo:
        info = SchemaInfo()
        for (table,) in self._all(_TABLES_SQL):
            info.tables[table] = []
        for table, column, type_name in self._all(_COLUMNS_SQL):
            if table in info.tables:
                info.tables[table].append(ColumnInfo(column, type_name.upper()))
        for table in info.tables:
            info.primary_keys[table] = set()
        for table, column in self._all(_PK_SQL):
            info.primary_keys.setdefault(table, set()).add(column)
        info.foreign_keys = [(t, c, rt, rc) for t, c, rt, rc in self._all(_FK_SQL)]
        return info

    def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        sql = f"SELECT DISTINCT TOP ({int(limit)}) {quote_ident(column)} FROM {_SCHEMA}.{quote_ident(table)} WHERE {quote_ident(column)} IS NOT NULL"
        return [normalize_value(r[0]) for r in self._all(sql)]

    def fingerprint(self) -> dict[str, str]:
        out = {}
        for table in self.load_schema().tables:
            count, checksum = self._all(f"SELECT COUNT_BIG(*), CHECKSUM_AGG(CHECKSUM(*)) FROM {_SCHEMA}.{quote_ident(table)}")[0]
            out[table] = f"{count}:{checksum}"
        return out

    def read_only_status(self) -> ReadOnlyStatus:
        values = self._all(_READ_ONLY_SQL)[0]
        granted = [label for label, v in zip(_READ_ONLY_LABELS, values) if v]
        return ReadOnlyStatus(not granted, "login can write: " + ", ".join(granted) if granted else "login has no write permissions")

    def ping(self) -> None:
        self._all("SELECT 1", timeout_s=10)
