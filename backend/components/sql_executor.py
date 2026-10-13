
import sqlite3
import time
from types import SimpleNamespace

from backend.config import DB_PATH

class SqlExecutor:
    def execute(self, sql: str):
        start = time.time()
        try:
            conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
            cur = conn.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            conn.close()
            return SimpleNamespace(rows=[list(r) for r in rows], columns=cols, row_count=len(rows), latency_ms=int((time.time()-start)*1000), error=None)
        except Exception as e:
            return SimpleNamespace(rows=[], columns=[], row_count=0, latency_ms=int((time.time()-start)*1000), error=str(e))

def execute_sql(sql):
    return SqlExecutor().execute(sql)
