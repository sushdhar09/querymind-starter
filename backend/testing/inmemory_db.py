"""In-memory stand-in for Azure SQL, for tests, CI and offline evaluation. NOT a substitute for the real server.

It executes T-SQL by transpiling it to SQLite, behind a read-only login, and deliberately rejects the things SQL Server
rejects but SQLite accepts (LIMIT, SQLite-only functions, select aliases in WHERE/GROUP BY/HAVING) and emulates two
semantics that differ (case-insensitive text comparison, integer AVG). Anything else is only as faithful as sqlglot's
transpiler; verify against a real server with `python data/check_azure.py`.
"""
import sqlite3
import threading
import time
import zlib
from datetime import date
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from backend import demo_data
from backend.db import ColumnInfo, DatabaseError, QueryResult, QueryTimeout, ReadOnlyStatus, SchemaInfo, normalize_value

SQLITE_ONLY_FUNCTIONS = {"strftime", "julianday", "date", "datetime", "ifnull", "substr", "group_concat", "random", "printf",
                         "instr", "typeof", "unixepoch"}
_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
_SQLITE_TYPES = {"INT": "INTEGER", "FLOAT": "REAL", "DATE": "TEXT", "NVARCHAR": "TEXT COLLATE NOCASE"}
PERMISSION_DENIED = "The user does not have permission to perform this action."


class _TSqlAvg:
    """AVG over integers truncates toward zero in T-SQL; over floats it is an ordinary mean."""

    def __init__(self):
        self.total, self.count, self.all_int = 0, 0, True

    def step(self, value):
        if value is None:
            return
        self.total += value
        self.count += 1
        self.all_int = self.all_int and isinstance(value, int)

    def finalize(self):
        if not self.count:
            return None
        return int(self.total / self.count) if self.all_int else self.total / self.count


def _part(index: int):
    def fn(value):
        try:
            return int(str(value)[:10].split("-")[index])
        except (ValueError, IndexError):
            return None
    return fn


class InMemoryDatabase:
    dialect = "tsql"

    def __init__(self, rows: Optional[dict[str, list[tuple]]] = None, today: Optional[date] = None):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._column_names = {c.name.lower() for t in demo_data.SCHEMA for c in t.columns}
        for table in demo_data.SCHEMA:
            cols = []
            for c in table.columns:
                kind = _SQLITE_TYPES[demo_data.base_type(c.sql_type)]
                cols.append(f"{c.name} {kind}{' PRIMARY KEY' if c.primary_key else ''}{' NOT NULL' if not c.nullable else ''}")
            self._conn.execute(f"CREATE TABLE {table.name} ({', '.join(cols)})")
        data = rows if rows is not None else demo_data.generate(today=today)
        for table in demo_data.SCHEMA:
            table_rows = [tuple(v.isoformat() if isinstance(v, date) else v for v in row) for row in data.get(table.name, [])]
            if table_rows:
                self._conn.executemany(f"INSERT INTO {table.name} VALUES ({','.join('?' * len(table.columns))})", table_rows)
        self._conn.commit()
        self._conn.create_aggregate("AVG", 1, _TSqlAvg)
        for name, index in (("YEAR", 0), ("MONTH", 1), ("DAY", 2)):
            self._conn.create_function(name, 1, _part(index), deterministic=True)
        self._conn.execute("PRAGMA query_only = ON")
        self._conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY)

    def _translate(self, sql: str) -> str:
        try:
            tokens = sqlglot.tokenize(sql, read="tsql")
            statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
        except SqlglotError as e:
            raise DatabaseError(f"Incorrect syntax: {str(e).splitlines()[0]}") from e
        if any(t.token_type == TokenType.LIMIT for t in tokens):
            raise DatabaseError("Incorrect syntax near 'LIMIT'.")
        if len(statements) != 1 or isinstance(statements[0], exp.Execute):
            raise DatabaseError(PERMISSION_DENIED)
        tree = statements[0]
        for token, following in zip(tokens, tokens[1:]):
            if token.text.lower() in SQLITE_ONLY_FUNCTIONS and following.token_type == TokenType.L_PAREN:
                raise DatabaseError(f"'{token.text.lower()}' is not a recognized built-in function name.")
        for select in tree.find_all(exp.Select):
            aliases = {e.alias.lower() for e in select.expressions if isinstance(e, exp.Alias)} - self._column_names
            for clause in ("where", "group", "having"):
                node = select.args.get(clause)
                for col in (node.find_all(exp.Column) if node is not None else ()):
                    if not col.table and col.name.lower() in aliases:
                        raise DatabaseError(f"Invalid column name '{col.name}'.")
        for table in tree.find_all(exp.Table):
            if table.args.get("db") is not None and table.args["db"].name.lower() == "dbo":
                table.set("db", None)
        try:
            return tree.sql(dialect="sqlite")
        except SqlglotError as e:
            raise DatabaseError(f"Could not execute statement: {e}") from e

    def query(self, sql: str, max_rows: int, timeout_s: float) -> QueryResult:
        translated = self._translate(sql)
        deadline = time.monotonic() + timeout_s
        with self._lock:
            self._conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            try:
                cur = self._conn.execute(translated)
                fetched = cur.fetchmany(max_rows + 1)
                columns = [d[0] for d in cur.description] if cur.description else []
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e).lower():
                    raise QueryTimeout(f"Query exceeded the {timeout_s:g}s time limit") from e
                raise DatabaseError(str(e)) from e
            except sqlite3.Error as e:
                raise DatabaseError(PERMISSION_DENIED if "not authorized" in str(e).lower() else str(e)) from e
            finally:
                self._conn.set_progress_handler(None, 0)
        return QueryResult(columns, [[normalize_value(v) for v in row] for row in fetched[:max_rows]], len(fetched) > max_rows)

    def load_schema(self) -> SchemaInfo:
        info = SchemaInfo()
        for table in demo_data.SCHEMA:
            info.tables[table.name] = [ColumnInfo(c.name, demo_data.base_type(c.sql_type)) for c in table.columns]
            info.primary_keys[table.name] = {c.name for c in table.columns if c.primary_key}
            info.foreign_keys += [(table.name, c.name, *c.references.split(".")) for c in table.columns if c.references]
        return info

    def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        with self._lock:
            rows = self._conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT ?', (limit,)).fetchall()
        return [normalize_value(r[0]) for r in rows]

    def fingerprint(self) -> dict[str, str]:
        out = {}
        with self._lock:
            for table in demo_data.TABLE_NAMES:
                rows = self._conn.execute(f'SELECT * FROM "{table}"').fetchall()
                out[table] = f"{len(rows)}:{sum(zlib.crc32(repr(r).encode()) for r in rows) % 2**32}"
        return out

    def read_only_status(self) -> ReadOnlyStatus:
        return ReadOnlyStatus(True, "in-memory test double (read-only)")

    def ping(self) -> None:
        return None
