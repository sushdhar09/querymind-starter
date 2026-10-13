import logging
import sqlite3

from backend.schemas import Example, RetrieverOutput
from backend.store import AppStore

log = logging.getLogger(__name__)


class ExampleRetriever:
    def __init__(self, store: AppStore):
        self.store = store

    def retrieve(self, nl_query: str, top_k: int = 3) -> RetrieverOutput:
        try:
            records = self.store.list_examples(limit=top_k)
        except sqlite3.Error as e:
            log.warning("Example retrieval failed: %s", e)
            return RetrieverOutput()
        return RetrieverOutput(examples=[Example(question=r.question, sql=r.sql) for r in records])
