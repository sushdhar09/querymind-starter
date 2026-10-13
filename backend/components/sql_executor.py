
import sqlite3
from pathlib import Path
import time
from types import SimpleNamespace

def find_db():
    candidates = [Path(__file__).parent.parent.parent / "data" / "querymind.db", Path("data/querymind.db"), Path("../data/querymind.db"), Path(__file__).parent / "querymind.db"]
    for p in candidates:
        if p.exists():
            return p
    return Path("data/querymind.db")

class SqlExecutor:
    def execute(self, sql: str):
        start = time.time()
        try:
            db_path = find_db()
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            conn.close()
            return SimpleNamespace(rows=[list(r) for r in rows], columns=cols, row_count=len(rows), latency_ms=int((time.time()-start)*1000), error=None)
        except Exception as e:
            return SimpleNamespace(rows=[], columns=[], row_count=0, latency_ms=int((time.time()-start)*1000), error=str(e))

def execute_sql(sql):
    return SqlExecutor().execute(sql)
