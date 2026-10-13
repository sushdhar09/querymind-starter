import logging
from typing import Optional
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
from backend.schemas import LogIn, PipelineResponse, QueryRequest
from backend.store import AppStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

store = AppStore().init()


def build_pipeline() -> Pipeline:
    return Pipeline(QueryClassifier(), SchemaLinker(), ExampleRetriever(store), SqlGenerator(),
                    SqlValidator(guardrails=store.get_guardrails), SqlExecutor(), ResultFormatter())


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
    response = pipeline.run(request)
    try:
        store.add_log(LogIn(nl_query=request.nl_query, generated_sql=response.generated_sql, status=response.status,
                            query_type=response.query_type, failed_stage=response.failed_stage,
                            latency_ms=response.total_latency_ms, row_count=response.execution.row_count,
                            error=response.error, model=store.get_model_config().provider))
    except Exception:
        log.exception("Failed to write query log")
    return response


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
def examples(limit: int = 100, offset: int = 0, query_type: Optional[str] = None, search: Optional[str] = None):
    return store.list_examples(limit, offset, query_type, search)


@router.get("/admin/logs")
def logs(limit: int = 100, offset: int = 0, status: Optional[str] = None, search: Optional[str] = None):
    return store.list_logs(limit, offset, status, search)


@router.get("/admin/guardrails")
def guardrails():
    return store.get_guardrails()


@router.get("/admin/config")
def config():
    return store.get_model_config()

app = FastAPI(title="QueryMind AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "db_exists": DB_PATH.exists()}
