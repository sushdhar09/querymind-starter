import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.config import CORS_ORIGINS, DATA_DIR, DB_PATH, EMBEDDING_MODE, OPENAI_API_KEY, ANTHROPIC_API_KEY
from backend.factory import build_app
from backend.pipeline import Pipeline
from backend.schemas import (
    ColumnMeta, ColumnMetaIn, Example, ExampleIn, ExampleRecord, Guardrails, LogIn, LogRecord, LogStats, ModelConfig,
    ModelConfigOut, PipelineResponse, QueryRequest, SchemaTable,
)
from backend.security import query_rate_limit, require_admin
from backend.store import AppStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

store = AppStore(analytics_db=None, metadata_seed=DATA_DIR / "column_metadata.json",
                 examples_seed=DATA_DIR / "examples_seed.json").init()
_app = build_app(store, EMBEDDING_MODE)
index, retriever, validator = _app.index, _app.retriever, _app.validator


_pipeline = _app.pipeline


def get_pipeline() -> Pipeline:
    return _pipeline


def provider_key_configured(provider: str) -> bool:
    return {"openai": bool(OPENAI_API_KEY), "anthropic": bool(ANTHROPIC_API_KEY)}.get(provider, True)


public = APIRouter()
admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@public.post("/query", response_model=PipelineResponse, dependencies=[Depends(query_rate_limit)])
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


@admin.get("/schema", response_model=list[SchemaTable])
def get_schema():
    try:
        return index.describe()
    except FileNotFoundError:
        raise HTTPException(503, f"Analytics database not found at {DB_PATH}. Run python data/seed_db.py")


@admin.put("/schema/{table}/{column}", response_model=ColumnMeta)
def update_column(table: str, column: str, meta: ColumnMetaIn):
    match = next(((t.table_name, c.name) for t in get_schema() if t.table_name.lower() == table.lower()
                  for c in t.columns if c.name.lower() == column.lower()), None)
    if not match:
        raise HTTPException(404, f"Unknown column '{table}.{column}'")
    return store.upsert_column_metadata(*match, meta)


def _validated_example(ex: ExampleIn) -> ExampleIn:
    result = validator.validate(ex.sql)
    if not result.valid:
        raise HTTPException(422, {"message": "Example SQL failed validation", "errors": result.errors})
    return ex


@admin.get("/examples", response_model=list[ExampleRecord])
def list_examples(limit: int = 100, offset: int = 0, query_type: Optional[str] = None, search: Optional[str] = None):
    return store.list_examples(min(limit, 1000), offset, query_type, search)


@admin.get("/examples/search", response_model=list[Example])
def search_examples(q: str, top_k: int = 5, query_type: Optional[str] = None):
    return retriever.search(q, min(max(top_k, 1), 20), query_type)


@admin.post("/examples", response_model=ExampleRecord, status_code=201)
def create_example(ex: ExampleIn):
    return store.create_example(_validated_example(ex))


@admin.put("/examples/{example_id}", response_model=ExampleRecord)
def update_example(example_id: int, ex: ExampleIn):
    updated = store.update_example(example_id, _validated_example(ex))
    if not updated:
        raise HTTPException(404, "Example not found")
    return updated


@admin.delete("/examples/{example_id}", status_code=204)
def delete_example(example_id: int):
    if not store.delete_example(example_id):
        raise HTTPException(404, "Example not found")


@admin.get("/logs", response_model=list[LogRecord])
def list_logs(response: Response, limit: int = 100, offset: int = 0, status: Optional[str] = None, search: Optional[str] = None):
    response.headers["X-Total-Count"] = str(store.count_logs(status, search))
    return store.list_logs(min(limit, 1000), offset, status, search)


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@admin.get("/logs/export")
def export_logs(status: Optional[str] = None, search: Optional[str] = None, limit: int = 10000):
    fields = ["id", "created_at", "nl_query", "generated_sql", "status", "query_type", "failed_stage", "latency_ms",
              "row_count", "error", "model"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(fields)
    for rec in store.list_logs(min(limit, 100000), 0, status, search):
        writer.writerow([_csv_safe(getattr(rec, f)) for f in fields])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="query_logs.csv"'})


@admin.get("/stats", response_model=LogStats)
def stats():
    return store.log_stats()


@admin.get("/guardrails", response_model=Guardrails)
def get_guardrails():
    return store.get_guardrails()


@admin.put("/guardrails", response_model=Guardrails)
def set_guardrails(g: Guardrails):
    return store.set_guardrails(g)


def _config_out(cfg: ModelConfig) -> ModelConfigOut:
    return ModelConfigOut(**cfg.model_dump(), api_key_configured=provider_key_configured(cfg.provider))


@admin.get("/config", response_model=ModelConfigOut)
def get_config():
    return _config_out(store.get_model_config())


@admin.put("/config", response_model=ModelConfigOut)
def set_config(cfg: ModelConfig):
    if not provider_key_configured(cfg.provider):
        raise HTTPException(422, f"The server has no API key configured for provider '{cfg.provider}'")
    return _config_out(store.set_model_config(cfg))


app = FastAPI(title="QueryMind AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-API-Key", "Authorization"],
                   expose_headers=["X-Total-Count"])
for prefix in ("", "/api/v1"):
    app.include_router(public, prefix=prefix)
    app.include_router(admin, prefix=prefix)


@app.get("/health")
def health():
    return {"status": "ok", "db_exists": DB_PATH.exists(), "llm_provider": store.get_model_config().provider}
