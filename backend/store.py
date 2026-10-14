import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from backend.config import APP_DB_PATH, DB_PATH, LLM_PROVIDER
from backend.schemas import (
    ColumnMeta, ColumnMetaIn, ExampleIn, ExampleRecord, Guardrails, LogIn, LogRecord, ModelConfig,
)

log = logging.getLogger(__name__)

MIGRATIONS = [
    """
    CREATE TABLE examples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        sql TEXT NOT NULL,
        query_type TEXT NOT NULL DEFAULT 'SELECT_SIMPLE',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
    );
    CREATE TABLE column_metadata (
        table_name TEXT NOT NULL,
        column_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        synonyms TEXT NOT NULL DEFAULT '[]',
        is_sensitive INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (table_name, column_name)
    );
    CREATE TABLE query_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nl_query TEXT NOT NULL,
        generated_sql TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        query_type TEXT,
        failed_stage TEXT,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        row_count INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        model TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
    );
    CREATE INDEX idx_logs_created ON query_logs(created_at);
    CREATE INDEX idx_logs_status ON query_logs(status);
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]


class AppStore:
    def __init__(self, path: Path = APP_DB_PATH, analytics_db: Path = DB_PATH, metadata_seed: Optional[Path] = None):
        self.path = Path(path)
        self.analytics_db = Path(analytics_db)
        self.metadata_seed = Path(metadata_seed) if metadata_seed else None

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> "AppStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode = WAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                c.executescript(script)
                c.execute(f"PRAGMA user_version = {i}")
        self._seed_column_metadata()
        self._import_legacy()
        return self

    def _seed_column_metadata(self) -> None:
        if not self.metadata_seed or not self.metadata_seed.exists():
            return
        data = json.loads(self.metadata_seed.read_text(encoding="utf-8"))
        with self._conn() as c:
            for table, cols in data.items():
                for col, m in cols.items():
                    c.execute("INSERT OR IGNORE INTO column_metadata VALUES (?,?,?,?,?)",
                              (table, col, m.get("description", ""), json.dumps(m.get("synonyms", [])), int(m.get("is_sensitive", False))))

    def sensitive_columns(self) -> set[str]:
        with self._conn() as c:
            rows = c.execute("SELECT table_name, column_name FROM column_metadata WHERE is_sensitive = 1").fetchall()
        return {f"{r[0]}.{r[1]}".lower() for r in rows}

    def _import_legacy(self) -> None:
        if not self.analytics_db.exists():
            return
        with self._conn() as c:
            has_examples = c.execute("SELECT 1 FROM examples LIMIT 1").fetchone()
            has_meta = c.execute("SELECT 1 FROM column_metadata LIMIT 1").fetchone()
            if has_examples and has_meta:
                return
            src = sqlite3.connect(f"file:{self.analytics_db.as_posix()}?mode=ro", uri=True)
            try:
                if not has_examples:
                    rows = src.execute("SELECT question, sql, COALESCE(query_type,'SELECT_SIMPLE') FROM few_shot_examples ORDER BY id").fetchall()
                    seen = set()
                    for q, s, t in rows:
                        if (q, s) not in seen:
                            seen.add((q, s))
                            c.execute("INSERT INTO examples (question, sql, query_type) VALUES (?,?,?)", (q, s, t))
                if not has_meta:
                    for t, col, desc, sens, syn in src.execute("SELECT table_name, column_name, description, is_sensitive, synonyms FROM schema_descriptions"):
                        syns = [s.strip() for s in (syn or "").split(",") if s.strip()]
                        c.execute("INSERT OR IGNORE INTO column_metadata VALUES (?,?,?,?,?)", (t, col, desc or "", json.dumps(syns), int(bool(sens))))
            except sqlite3.Error as e:
                log.warning("Legacy import skipped: %s", e)
            finally:
                src.close()

    @staticmethod
    def _example(r: sqlite3.Row) -> ExampleRecord:
        return ExampleRecord(**dict(r))

    def list_examples(self, limit: int = 100, offset: int = 0, query_type: Optional[str] = None,
                      search: Optional[str] = None) -> list[ExampleRecord]:
        sql, args = "SELECT * FROM examples WHERE 1=1", []
        if query_type:
            sql += " AND query_type = ?"
            args.append(query_type)
        if search:
            sql += " AND (question LIKE ? OR sql LIKE ?)"
            args += [f"%{search}%"] * 2
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return [self._example(r) for r in rows]

    def get_example(self, example_id: int) -> Optional[ExampleRecord]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM examples WHERE id = ?", (example_id,)).fetchone()
        return self._example(r) if r else None

    def create_example(self, ex: ExampleIn) -> ExampleRecord:
        with self._conn() as c:
            cur = c.execute("INSERT INTO examples (question, sql, query_type) VALUES (?,?,?)", (ex.question, ex.sql, ex.query_type))
            return self._example(c.execute("SELECT * FROM examples WHERE id = ?", (cur.lastrowid,)).fetchone())

    def update_example(self, example_id: int, ex: ExampleIn) -> Optional[ExampleRecord]:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE examples SET question=?, sql=?, query_type=?, updated_at=strftime('%Y-%m-%d %H:%M:%S','now') WHERE id=?",
                (ex.question, ex.sql, ex.query_type, example_id))
            if not cur.rowcount:
                return None
            return self._example(c.execute("SELECT * FROM examples WHERE id = ?", (example_id,)).fetchone())

    def delete_example(self, example_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM examples WHERE id = ?", (example_id,)).rowcount > 0

    @staticmethod
    def _meta(r: sqlite3.Row) -> ColumnMeta:
        return ColumnMeta(table_name=r["table_name"], column_name=r["column_name"], description=r["description"],
                          synonyms=json.loads(r["synonyms"]), is_sensitive=bool(r["is_sensitive"]))

    def list_column_metadata(self) -> list[ColumnMeta]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM column_metadata ORDER BY table_name, column_name").fetchall()
        return [self._meta(r) for r in rows]

    def upsert_column_metadata(self, table: str, column: str, meta: ColumnMetaIn) -> ColumnMeta:
        with self._conn() as c:
            c.execute(
                "INSERT INTO column_metadata VALUES (?,?,?,?,?) ON CONFLICT(table_name, column_name) DO UPDATE SET "
                "description=excluded.description, synonyms=excluded.synonyms, is_sensitive=excluded.is_sensitive",
                (table, column, meta.description, json.dumps(meta.synonyms), int(meta.is_sensitive)))
            return self._meta(c.execute("SELECT * FROM column_metadata WHERE table_name=? AND column_name=?", (table, column)).fetchone())

    def add_log(self, entry: LogIn) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO query_logs (nl_query, generated_sql, status, query_type, failed_stage, latency_ms, row_count, error, model) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (entry.nl_query, entry.generated_sql, entry.status, entry.query_type, entry.failed_stage,
                 entry.latency_ms, entry.row_count, entry.error, entry.model))
            return cur.lastrowid

    def list_logs(self, limit: int = 100, offset: int = 0, status: Optional[str] = None,
                  search: Optional[str] = None) -> list[LogRecord]:
        sql, args = "SELECT * FROM query_logs WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if search:
            sql += " AND (nl_query LIKE ? OR generated_sql LIKE ?)"
            args += [f"%{search}%"] * 2
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return [LogRecord(**dict(r)) for r in rows]

    def _get_setting(self, key: str) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None

    def _set_setting(self, key: str, value: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def get_guardrails(self) -> Guardrails:
        data = self._get_setting("guardrails")
        return Guardrails(**data) if data else Guardrails()

    def set_guardrails(self, g: Guardrails) -> Guardrails:
        self._set_setting("guardrails", g.model_dump())
        return g

    def get_model_config(self) -> ModelConfig:
        data = self._get_setting("model_config")
        return ModelConfig(**data) if data else ModelConfig(provider=LLM_PROVIDER if LLM_PROVIDER in ("mock", "openai", "anthropic", "ollama") else "mock")

    def set_model_config(self, m: ModelConfig) -> ModelConfig:
        self._set_setting("model_config", m.model_dump())
        return m
