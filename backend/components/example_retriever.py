import hashlib
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import chromadb

from backend.config import CHROMA_PATH
from backend.schemas import Example, RetrieverOutput
from backend.store import AppStore

log = logging.getLogger(__name__)

COLLECTION = "examples"


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class ExampleRetriever:
    def __init__(self, store: AppStore, chroma_path: Path = CHROMA_PATH, embedding_function=None):
        self.store = store
        self.chroma_path = Path(chroma_path)
        self.embedding_function = embedding_function
        self._collection = None
        self._synced_version: Optional[int] = None
        self._lock = threading.Lock()

    def _get_collection(self):
        if self._collection is None:
            self.chroma_path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.chroma_path))
            kwargs = {"embedding_function": self.embedding_function} if self.embedding_function else {}
            self._collection = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"}, **kwargs)
        return self._collection

    def sync(self) -> int:
        with self._lock:
            version = self.store.examples_version()
            if version == self._synced_version:
                return 0
            col = self._get_collection()
            wanted = {str(e.id): e for e in self.store.list_examples(limit=100000)}
            hashes = {i: hashlib.sha1(f"{e.question}\n{e.sql}\n{e.query_type}".encode()).hexdigest() for i, e in wanted.items()}
            existing = col.get(include=["metadatas"])
            current = {i: (m or {}).get("hash") for i, m in zip(existing["ids"], existing["metadatas"])}
            stale = [i for i in current if i not in wanted]
            changed = [i for i in wanted if current.get(i) != hashes[i]]
            if stale:
                col.delete(ids=stale)
            if changed:
                col.upsert(ids=changed, documents=[wanted[i].question for i in changed],
                           metadatas=[{"sql": wanted[i].sql, "query_type": wanted[i].query_type, "hash": hashes[i]} for i in changed])
            self._synced_version = version
            return len(changed) + len(stale)

    def search(self, nl_query: str, top_k: int = 3, query_type: Optional[str] = None) -> list[Example]:
        self.sync()
        col = self._get_collection()
        if col.count() == 0:
            return []
        res = col.query(query_texts=[nl_query], n_results=min(top_k, col.count()),
                        where={"query_type": query_type} if query_type else None, include=["documents", "metadatas", "distances"])
        return [Example(question=doc, sql=meta["sql"], score=round(1 - dist, 4))
                for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])]

    def _lexical(self, nl_query: str, top_k: int) -> list[Example]:
        q = _tokens(nl_query)
        scored = []
        for e in self.store.list_examples(limit=100000):
            t = _tokens(e.question)
            scored.append((len(q & t) / (len(q | t) or 1), e))
        scored.sort(key=lambda s: -s[0])
        return [Example(question=e.question, sql=e.sql, score=round(s, 4)) for s, e in scored[:top_k]]

    def retrieve(self, nl_query: str, top_k: int = 3) -> RetrieverOutput:
        try:
            return RetrieverOutput(examples=self.search(nl_query, top_k))
        except Exception as e:
            log.warning("Vector retrieval failed (%s); falling back to lexical ranking", e)
        try:
            return RetrieverOutput(examples=self._lexical(nl_query, top_k))
        except sqlite3.Error as e:
            log.warning("Example retrieval failed: %s", e)
            return RetrieverOutput()
