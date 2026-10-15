import sqlite3
import time
import re
import pyodbc
from pathlib import Path
from typing import Optional

from backend.config import ABSOLUTE_MAX_ROWS, DB_PATH, QUERY_TIMEOUT_S, DB_TYPE, ROOT_DIR
from backend.schemas import ExecutorOutput

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY

def _get_azure_conn_str():
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("AZURE_SQL_CONNECTION_STRING="):
                val = line.split("=", 1)[1].strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                return val
    return None


def _translate_to_mssql(sql: str) -> str:
    s = sql.strip().rstrip(";")

    # Fix SQLite DATE / DATETIME functions
    # DATE('now') / DATE('now','-30 days') -> CAST(GETDATE() AS DATE)
    s = re.sub(r"DATE\s*\(\s*['\"]now['\"]\s*\)", "CAST(GETDATE() AS DATE)", s, flags=re.IGNORECASE)
    s = re.sub(r"DATETIME\s*\(\s*['\"]now['\"]\s*\)", "GETDATE()", s, flags=re.IGNORECASE)
    s = re.sub(r"DATE\s*\(\s*['\"]now['\"]\s*,\s*['\"]-(\d+)\s+days['\"]\s*\)",
               r"CAST(DATEADD(day, -\1, GETDATE()) AS DATE)", s, flags=re.IGNORECASE)

    # DATE(column) -> CAST(column AS DATE)
    s = re.sub(r"\bDATE\s*\(\s*([a-zA-Z_][a-zA-Z0-9_\.]*)\s*\)", r"CAST(\1 AS DATE)", s, flags=re.IGNORECASE)

    # strftime('%Y', col) -> YEAR(col), strftime('%m') -> MONTH(col) etc
    s = re.sub(r"strftime\s*\(\s*['\"]%Y['\"]\s*,\s*([^)]+)\)", r"YEAR(\1)", s, flags=re.IGNORECASE)
    s = re.sub(r"strftime\s*\(\s*['\"]%m['\"]\s*,\s*([^)]+)\)", r"MONTH(\1)", s, flags=re.IGNORECASE)
    s = re.sub(r"strftime\s*\(\s*['\"]%d['\"]\s*,\s*([^)]+)\)", r"DAY(\1)", s, flags=re.IGNORECASE)

    # LIMIT translation
    m = re.search(r'\sLIMIT\s+(\d+)\s*$', s, re.IGNORECASE)
    if m:
        limit_n = m.group(1)
        base = s[:m.start()]
        if re.search(r'\bORDER\s+BY\b', base, re.IGNORECASE):
            return f"{base} OFFSET 0 ROWS FETCH NEXT {limit_n} ROWS ONLY"
        else:
            return re.sub(r'^\s*SELECT\s+(DISTINCT\s+)?',
                          lambda mo: f"SELECT {mo.group(1) or ''}TOP {limit_n} ",
                          base, flags=re.IGNORECASE, count=1)
    return s

class SqlExecutor:
    def __init__(self, db_path: Path = DB_PATH, timeout_s: Optional[float] = None, max_fetch_rows: int = ABSOLUTE_MAX_ROWS):
        self.db_path = db_path
        self.timeout_s = QUERY_TIMEOUT_S if timeout_s is None else timeout_s
        self.max_fetch_rows = max_fetch_rows
        self.db_type = DB_TYPE.lower()

    def _connect_sqlite(self, deadline: float) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        return conn

    def _connect_azure(self):
        conn_str = _get_azure_conn_str()
        if not conn_str:
            raise ValueError("AZURE_SQL_CONNECTION_STRING not found in.env")
        conn = pyodbc.connect(conn_str, timeout=int(self.timeout_s))
        return conn

    def execute(self, sql: str, max_rows: Optional[int] = None) -> ExecutorOutput:
        start = time.monotonic()
        limit = min(max_rows or self.max_fetch_rows, self.max_fetch_rows)

        upper = sql.strip().upper()
        if not upper.startswith("SELECT") and not upper.startswith("WITH"):
            return ExecutorOutput(latency_ms=int((time.monotonic() - start) * 1000), error="Only SELECT queries allowed")

        if self.db_type == 'azuresql':
            sql = _translate_to_mssql(sql)

        try:
            if self.db_type == 'azuresql':
                conn = self._connect_azure()
                try:
                    cur = conn.cursor()
                    cur.execute(sql)
                    fetched = cur.fetchmany(limit + 1)
                    cols = [d[0] for d in cur.description] if cur.description else []
                finally:
                    conn.close()
            else:
                conn = self._connect_sqlite(start + self.timeout_s)
                try:
                    cur = conn.execute(sql)
                    fetched = cur.fetchmany(limit + 1)
                    cols = [d[0] for d in cur.description] if cur.description else []
                finally:
                    conn.close()
        except Exception as e:
            msg = f"Query exceeded the {self.timeout_s:g}s time limit" if "interrupted" in str(e).lower() else str(e)
            return ExecutorOutput(latency_ms=int((time.monotonic() - start) * 1000), error=msg)

        truncated = len(fetched) > limit
        rows = [list(r) for r in fetched[:limit]]
        return ExecutorOutput(rows=rows, columns=cols, row_count=len(rows), truncated=truncated,
                              latency_ms=int((time.monotonic() - start) * 1000))