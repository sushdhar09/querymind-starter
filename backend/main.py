import logging
import sqlite3

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.components.example_retriever import ExampleRetriever
from backend.components.query_classifier import QueryClassifier
from backend.components.result_formatter import ResultFormatter
from backend.components.schema_linker import SchemaLinker
from backend.components.sql_executor import SqlExecutor
from backend.components.sql_generator import SqlGenerator
from backend.components.sql_validator import SqlValidator
from backend.config import DB_PATH
from backend.pipeline import Pipeline
from backend.schemas import PipelineResponse, QueryRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_pipeline() -> Pipeline:
    return Pipeline(QueryClassifier(), SchemaLinker(), ExampleRetriever(), SqlGenerator(), SqlValidator(),
                    SqlExecutor(), ResultFormatter())


_pipeline = build_pipeline()


def get_pipeline() -> Pipeline:
    return _pipeline


def get_conn(readonly: bool = True) -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found at {DB_PATH}. Run python data/seed_db.py")
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode={'ro' if readonly else 'rw'}", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


router = APIRouter()


@router.post("/query", response_model=PipelineResponse)
def query(request: QueryRequest, pipeline: Pipeline = Depends(get_pipeline)):
    return pipeline.run(request)


@router.get("/admin/schema")
def schema():
    conn = get_conn()
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        return [{"table_name": t["name"], "columns": [dict(c) for c in conn.execute(f'PRAGMA table_info("{t["name"]}")')]}
                for t in tables]
    finally:
        conn.close()


@router.get("/admin/examples")
def examples():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, question, sql, query_type FROM few_shot_examples ORDER BY id DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/admin/logs")
def logs(limit: int = 100):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, nl_query AS natural_language_query, generated_sql, status, latency_ms, row_count, error, timestamp "
            "FROM query_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/admin/guardrails")
def guardrails():
    return {"max_rows": 100, "blocked_keywords": ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"],
            "enable_self_healing": True}


@router.get("/admin/config")
def config():
    return {"llm_provider": "mock", "dialect": "sqlite", "temperature": 0, "max_rows": 100}


app = FastAPI(title="QueryMind AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "db_exists": DB_PATH.exists()}
