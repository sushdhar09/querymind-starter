import logging
import sqlite3
from pathlib import Path

from backend.config import DB_PATH
from backend.schemas import Example, RetrieverOutput

log = logging.getLogger(__name__)


class ExampleRetriever:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def retrieve(self, nl_query: str, top_k: int = 3) -> RetrieverOutput:
        try:
            conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT question, sql FROM few_shot_examples LIMIT ?", (top_k,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as e:
            log.warning("Example retrieval failed: %s", e)
            return RetrieverOutput()
        return RetrieverOutput(examples=[Example(question=q, sql=s) for q, s in rows])
