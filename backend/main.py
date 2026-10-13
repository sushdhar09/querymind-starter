
"""
QueryMind AI - FastAPI Backend
Universal version - serves BOTH /query and /api/v1/query to fix 404 errors
Fixed imports for uvicorn backend.main:app
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import time
from pathlib import Path
import sys
import os

# Add backend and project root to path
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(current_dir))

def get_db_path():
    candidates = [
        project_root / "data" / "querymind.db",
        current_dir / "data" / "querymind.db",
        Path("data/querymind.db"),
        Path("../data/querymind.db"),
    ]
    for p in candidates:
        if p.exists():
            return p.resolve()
    return candidates[0].resolve()

DB_PATH = get_db_path()
print(f"Using DB: {DB_PATH}")

def safe_import():
    comps = {}
    def try_import(mod_paths, cls_name):
        for mod_path in mod_paths:
            try:
                parts = mod_path.split(".")
                mod = __import__(mod_path, fromlist=[cls_name])
                cls = getattr(mod, cls_name)
                inst = cls()
                print(f"✅ Loaded {mod_path}.{cls_name}")
                return inst
            except Exception as e:
                # print(f"Try {mod_path} failed: {e}")
                continue
        print(f"❌ Failed to load {cls_name} from {mod_paths}")
        return None

    comps['classifier'] = try_import(
        ["backend.components.query_classifier", "components.query_classifier", "backend.components.classifier", "components.classifier"],
        "QueryClassifier"
    )
    comps['linker'] = try_import(
        ["backend.components.schema_linker", "components.schema_linker"],
        "SchemaLinker"
    )
    comps['retriever'] = try_import(
        ["backend.components.example_retriever", "components.example_retriever", "backend.components.retriever", "components.retriever"],
        "ExampleRetriever"
    )
    comps['generator'] = try_import(
        ["backend.components.sql_generator", "components.sql_generator", "backend.components.generator", "components.generator"],
        "SqlGenerator"
    )
    comps['validator'] = try_import(
        ["backend.components.sql_validator", "components.sql_validator", "backend.components.validator", "components.validator"],
        "SqlValidator"
    )
    comps['executor'] = try_import(
        ["backend.components.sql_executor", "components.sql_executor", "backend.components.executor", "components.executor"],
        "SqlExecutor"
    )
    comps['formatter'] = try_import(
        ["backend.components.result_formatter", "components.result_formatter", "backend.components.formatter", "components.formatter"],
        "ResultFormatter"
    )
    return comps

components = safe_import()
print(f"Components loaded: {list(k for k,v in components.items() if v)}")

app = FastAPI(title="QueryMind AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    nl_query: str = None
    natural_language_query: str = None
    query: str = None
    dialect: str = "sqlite"
    max_rows: int = 100

    def get_nl(self):
        return self.nl_query or self.natural_language_query or self.query or ""

def get_conn(readonly=True):
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found at {DB_PATH}. Run python data/seed.py or data/seed_db.py")
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def execute_pipeline(nl_query: str, max_rows: int = 100):
    start = time.time()
    
    if components['classifier']:
        try:
            if hasattr(components['classifier'], 'classify'):
                c_out = components['classifier'].classify(nl_query)
                query_type = getattr(c_out, 'query_type', 'SELECT_SIMPLE')
            else:
                query_type = components['classifier'](nl_query)
                query_type = query_type.get('query_type', 'SELECT_SIMPLE') if isinstance(query_type, dict) else 'SELECT_SIMPLE'
        except Exception as e:
            print(f"Classifier error: {e}")
            query_type = "SELECT_SIMPLE"
    else:
        ql = nl_query.lower()
        if any(kw in ql for kw in ["drop", "delete", "update", "insert", "truncate", "alter"]):
            query_type = "UNSUPPORTED"
        else:
            query_type = "SELECT_SIMPLE"

    if query_type == "UNSUPPORTED":
        return {
            "error": "Unsupported query type - destructive operations blocked",
            "query_type": query_type,
            "generated_sql": "",
            "execution": {"error": "Blocked"}
        }

    resolved = {"resolved_tables": [], "resolved_columns": [], "ambiguities": []}
    if components['linker']:
        try:
            try:
                linker_out = components['linker'].link(nl_query, None, DB_PATH)
            except:
                linker_out = components['linker'].link(nl_query)
            if hasattr(linker_out, 'model_dump'):
                resolved = linker_out.model_dump()
            elif isinstance(linker_out, dict):
                resolved = linker_out
            else:
                resolved = {"resolved_tables": getattr(linker_out, 'resolved_tables', []), "resolved_columns": [], "ambiguities": []}
        except Exception as e:
            print(f"Linker error: {e}")

    examples = []
    if components['retriever']:
        try:
            ret_out = components['retriever'].retrieve(nl_query, top_k=3)
            if hasattr(ret_out, 'examples'):
                examples = [{"question": e.question, "sql": e.sql} if hasattr(e, 'question') else e for e in ret_out.examples]
            else:
                examples = ret_out
        except Exception as e:
            print(f"Retriever error: {e}")

    generated_sql = "SELECT * FROM orders LIMIT 100"
    explanation = "Mock generator"
    if components['generator']:
        try:
            prompt = nl_query
            if hasattr(components['generator'], 'build_prompt'):
                try:
                    class Dummy:
                        def __init__(self):
                            self.resolved_tables = resolved.get('resolved_tables', ['orders'])
                            self.resolved_columns = resolved.get('resolved_columns', [])
                            self.ambiguities = resolved.get('ambiguities', [])
                    dummy_linker = Dummy()
                    class DummyRet:
                        def __init__(self, ex):
                            from collections import namedtuple
                            Ex = namedtuple('Ex', ['question', 'sql'])
                            self.examples = [Ex(q['question'], q['sql']) if isinstance(q, dict) else q for q in ex]
                    dummy_ret = DummyRet(examples)
                    prompt = components['generator'].build_prompt(nl_query, dummy_linker, dummy_ret)
                except Exception as pe:
                    print(f"Build prompt error: {pe}")
                    prompt = nl_query
            
            gen_out = components['generator'].generate(prompt) if hasattr(components['generator'], 'generate') else components['generator'](prompt)
            if hasattr(gen_out, 'sql'):
                generated_sql = gen_out.sql
                explanation = getattr(gen_out, 'explanation', '')
            elif isinstance(gen_out, dict):
                generated_sql = gen_out.get('sql', generated_sql)
                explanation = gen_out.get('explanation', '')
            else:
                generated_sql = str(gen_out)
        except Exception as e:
            print(f"Generator error: {e}")
            import traceback; traceback.print_exc()

    sanitized_sql = generated_sql
    if components['validator']:
        try:
            val_out = components['validator'].validate(generated_sql, max_rows=max_rows)
            if hasattr(val_out, 'is_valid'):
                if not val_out.is_valid:
                    return {"error": val_out.errors, "generated_sql": generated_sql, "is_valid": False}
                sanitized_sql = val_out.sanitized_sql
            elif isinstance(val_out, dict):
                if not val_out.get('valid', True):
                    return {"error": val_out.get('errors'), "generated_sql": generated_sql}
                sanitized_sql = val_out.get('sanitized_sql', generated_sql)
        except Exception as e:
            print(f"Validator error: {e}")

    exec_result = {"rows": [], "columns": [], "row_count": 0, "latency_ms": 0}
    try:
        if components['executor']:
            exec_out = components['executor'].execute(sanitized_sql)
            if hasattr(exec_out, 'model_dump'):
                exec_result = exec_out.model_dump()
            elif isinstance(exec_out, dict):
                exec_result = exec_out
            else:
                exec_result = {"rows": getattr(exec_out, 'rows', []), "columns": getattr(exec_out, 'columns', []), "row_count": getattr(exec_out, 'row_count', 0), "latency_ms": getattr(exec_out, 'latency_ms', 0)}
        else:
            s = time.time()
            conn = get_conn(readonly=True)
            cur = conn.execute(sanitized_sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            exec_result = {
                "rows": [list(r) for r in rows],
                "columns": cols,
                "row_count": len(rows),
                "latency_ms": int((time.time() - s) * 1000)
            }
            conn.close()
    except Exception as e:
        exec_result = {"rows": [], "columns": [], "row_count": 0, "latency_ms": 0, "error": str(e)}

    formatted = {"table": [], "nl_summary": f"Found {exec_result.get('row_count',0)} rows"}
    if components['formatter']:
        try:
            class ExecObj:
                def __init__(self, d):
                    self.rows = d.get('rows', [])
                    self.columns = d.get('columns', [])
                    self.row_count = d.get('row_count', 0)
                    self.latency_ms = d.get('latency_ms', 0)
                    self.error = d.get('error')
            fmt_out = components['formatter'].format(ExecObj(exec_result), nl_query)
            if hasattr(fmt_out, 'model_dump'):
                formatted = fmt_out.model_dump(by_alias=True)
            elif isinstance(fmt_out, dict):
                formatted = fmt_out
            else:
                formatted = {"table": getattr(fmt_out, 'table', []), "nl_summary": getattr(fmt_out, 'nl_summary', ''), "columns": getattr(fmt_out, 'columns', []), "row_count": getattr(fmt_out, 'row_count', 0)}
        except Exception as e:
            print(f"Formatter error: {e}")
            cols = exec_result.get('columns', [])
            rows = exec_result.get('rows', [])
            formatted['table'] = [dict(zip(cols, r)) for r in rows[:50]]

    return {
        "natural_language_query": nl_query,
        "query_type": query_type,
        "resolved_entities": resolved,
        "few_shot_examples": examples,
        "generated_sql": sanitized_sql,
        "explanation": explanation,
        "result": formatted,
        "execution": exec_result,
        "total_latency_ms": int((time.time() - start) * 1000)
    }

@app.get("/")
def root():
    return {"message": "QueryMind AI - Text-to-SQL Engine", "docs": "/docs", "health": "/health", "db_path": str(DB_PATH), "db_exists": DB_PATH.exists(), "components": list(k for k,v in components.items() if v)}

@app.get("/health")
def health():
    return {"status": "ok", "db_exists": DB_PATH.exists(), "db_path": str(DB_PATH), "components": list(k for k,v in components.items() if v)}

def handle_query(request: QueryRequest):
    nl = request.get_nl()
    if not nl:
        return {"error": "No query provided. Send nl_query field"}
    return execute_pipeline(nl, request.max_rows)

@app.post("/query")
def query_old(request: QueryRequest):
    return handle_query(request)

@app.get("/admin/schema")
def schema_old():
    conn = get_conn()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    result = []
    for t in tables:
        cols = conn.execute(f"PRAGMA table_info({t['name']})").fetchall()
        result.append({"table_name": t['name'], "columns": [dict(c) for c in cols]})
    conn.close()
    return result

@app.get("/admin/examples")
def examples_old():
    conn = get_conn()
    rows = conn.execute("SELECT id, question, sql, query_type FROM few_shot_examples ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return [{"id": r[0], "question": r[1], "sql": r[2], "query_type": r[3]} for r in rows]

@app.get("/admin/logs")
def logs_old(limit: int = 100):
    try:
        conn = get_conn(readonly=False)
        rows = conn.execute("SELECT id, natural_language_query, generated_sql, status, latency_ms, row_count, error, timestamp FROM query_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []

@app.get("/admin/guardrails")
def guardrails_old():
    return {"max_rows": 100, "blocked_keywords": ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"], "enable_self_healing": True}

@app.get("/admin/config")
def config_old():
    return {"llm_provider": "mock", "dialect": "sqlite", "temperature": 0, "max_rows": 100}

@app.post("/api/v1/query")
def query_new(request: QueryRequest):
    return handle_query(request)

@app.get("/api/v1/admin/schema")
def schema_new():
    return schema_old()

@app.get("/api/v1/admin/examples")
def examples_new():
    return examples_old()

@app.get("/api/v1/admin/logs")
def logs_new(limit: int = 100):
    return logs_old(limit)

@app.get("/api/v1/admin/guardrails")
def guardrails_new():
    return guardrails_old()

@app.get("/api/v1/admin/config")
def config_new():
    return config_old()
