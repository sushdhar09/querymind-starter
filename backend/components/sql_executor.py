import sqlite3
import time
from pathlib import Path
from typing import Optional

from backend.config import ABSOLUTE_MAX_ROWS, DB_PATH, QUERY_TIMEOUT_S
from backend.schemas import ExecutorOutput

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


class SqlExecutor:
    def __init__(self, db_path: Path = DB_PATH, timeout_s: float = QUERY_TIMEOUT_S, max_fetch_rows: int = ABSOLUTE_MAX_ROWS):
        self.db_path = db_path
        self.timeout_s = timeout_s
        self.max_fetch_rows = max_fetch_rows

    def _connect(self, deadline: float) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        return conn

    def execute(self, sql: str, max_rows: Optional[int] = None) -> ExecutorOutput:
        start = time.monotonic()
        limit = min(max_rows or self.max_fetch_rows, self.max_fetch_rows)
        try:
            conn = self._connect(start + self.timeout_s)
            try:
                cur = conn.execute(sql)
                fetched = cur.fetchmany(limit + 1)
                cols = [d[0] for d in cur.description] if cur.description else []
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            msg = f"Query exceeded the {self.timeout_s:g}s time limit" if "interrupted" in str(e) else str(e)
            return ExecutorOutput(latency_ms=int((time.monotonic() - start) * 1000), error=msg)
        except sqlite3.Error as e:
            return ExecutorOutput(latency_ms=int((time.monotonic() - start) * 1000), error=str(e))
        truncated = len(fetched) > limit
        rows = [list(r) for r in fetched[:limit]]
        return ExecutorOutput(rows=rows, columns=cols, row_count=len(rows), truncated=truncated,
                              latency_ms=int((time.monotonic() - start) * 1000))
