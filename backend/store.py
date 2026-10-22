import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from config import APP_DB_PATH, LLM_MODEL, LLM_PROVIDER
from schemas import (
    INTERNAL_TABLES,
    ColumnMeta,
    ColumnMetaIn,
    ExampleIn,
    ExampleRecord,
    Guardrails,
    LogIn,
    LogRecord,
    LogStats,
    ModelConfig,
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
    """
    ALTER TABLE query_logs ADD COLUMN request_id TEXT;
    CREATE INDEX idx_logs_request ON query_logs(request_id);
    """,
]


class AppStore:
    def __init__(self, path: Path = APP_DB_PATH, metadata_seed: Optional[Path] = None,
                 examples_seed: Optional[Path] = None):
        self.path = Path(path)
        self.metadata_seed = Path(metadata_seed) if metadata_seed else None
        self.examples_seed = Path(examples_seed) if examples_seed else None

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
        self._seed_examples()
        return self

    def _seed_examples(self) -> None:
        if not self.examples_seed or not self.examples_seed.exists() or self._get_setting("examples_seeded"):
            return
        for ex in json.loads(self.examples_seed.read_text(encoding="utf-8")):
            with self._conn() as c:
                exists = c.execute("SELECT 1 FROM examples WHERE question = ?", (ex["question"],)).fetchone()
            if not exists:
                self.create_example(ExampleIn(**ex))
        self._set_setting("examples_seeded", {"done": True})

    def examples_version(self) -> int:
        with self._conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key = 'examples_version'").fetchone()
        return int(r["value"]) if r else 0

    @staticmethod
    def _bump_examples_version(c: sqlite3.Connection) -> None:
        c.execute("INSERT INTO settings VALUES ('examples_version', '1') ON CONFLICT(key) DO UPDATE SET "
                  "value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)")

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
            self._bump_examples_version(c)
            return self._example(c.execute("SELECT * FROM examples WHERE id = ?", (cur.lastrowid,)).fetchone())

    def update_example(self, example_id: int, ex: ExampleIn) -> Optional[ExampleRecord]:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE examples SET question=?, sql=?, query_type=?, updated_at=strftime('%Y-%m-%d %H:%M:%S','now') WHERE id=?",
                (ex.question, ex.sql, ex.query_type, example_id))
            if not cur.rowcount:
                return None
            self._bump_examples_version(c)
            return self._example(c.execute("SELECT * FROM examples WHERE id = ?", (example_id,)).fetchone())

    def delete_example(self, example_id: int) -> bool:
        with self._conn() as c:
            deleted = c.execute("DELETE FROM examples WHERE id = ?", (example_id,)).rowcount > 0
            if deleted:
                self._bump_examples_version(c)
            return deleted

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
                "INSERT INTO query_logs (nl_query, generated_sql, status, query_type, failed_stage, latency_ms, row_count, error, model, request_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (entry.nl_query, entry.generated_sql, entry.status, entry.query_type, entry.failed_stage,
                 entry.latency_ms, entry.row_count, entry.error, entry.model, entry.request_id))
            return cur.lastrowid

    @staticmethod
    def _log_filter(status: Optional[str], search: Optional[str]) -> tuple[str, list]:
        sql, args = " WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if search:
            sql += " AND (nl_query LIKE ? OR generated_sql LIKE ?)"
            args += [f"%{search}%"] * 2
        return sql, args

    def list_logs(self, limit: int = 100, offset: int = 0, status: Optional[str] = None,
                  search: Optional[str] = None) -> list[LogRecord]:
        where, args = self._log_filter(status, search)
        with self._conn() as c:
            rows = c.execute(f"SELECT * FROM query_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return [LogRecord(**dict(r)) for r in rows]

    def count_logs(self, status: Optional[str] = None, search: Optional[str] = None) -> int:
        where, args = self._log_filter(status, search)
        with self._conn() as c:
            return c.execute(f"SELECT COUNT(*) FROM query_logs{where}", args).fetchone()[0]

    def log_stats(self) -> LogStats:
        with self._conn() as c:
            by_status = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM query_logs GROUP BY status")}
            avg = c.execute("SELECT COALESCE(AVG(latency_ms), 0) FROM query_logs").fetchone()[0]
        total = sum(by_status.values())
        return LogStats(total=total, by_status=by_status, avg_latency_ms=round(avg, 1),
                        success_rate=round(by_status.get("success", 0) / total, 4) if total else 0.0)

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
        g = g.model_copy(update={"blocked_tables": list(dict.fromkeys([*g.blocked_tables, *INTERNAL_TABLES]))})
        self._set_setting("guardrails", g.model_dump())
        return g

    def get_model_config(self) -> ModelConfig:
        data = self._get_setting("model_config")
        if data:
            data["dialect"] = "tsql"
        return ModelConfig(**data) if data else ModelConfig(provider=LLM_PROVIDER if LLM_PROVIDER in ("mock", "openai", "anthropic", "ollama") else "mock", model=LLM_MODEL)

    def set_model_config(self, m: ModelConfig) -> ModelConfig:
        self._set_setting("model_config", m.model_dump())
        return m
