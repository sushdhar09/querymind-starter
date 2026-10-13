import sqlite3
import time
from pathlib import Path

from backend.config import DB_PATH
from backend.schemas import ExecutorOutput


class SqlExecutor:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def execute(self, sql: str) -> ExecutorOutput:
        start = time.time()
        try:
            conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
            try:
                cur = conn.execute(sql)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
            finally:
                conn.close()
            return ExecutorOutput(rows=[list(r) for r in rows], columns=cols, row_count=len(rows),
                                  latency_ms=int((time.time() - start) * 1000))
        except sqlite3.Error as e:
            return ExecutorOutput(latency_ms=int((time.time() - start) * 1000), error=str(e))
