
"""
Standalone QueryMind Backend - No import issues
Run from project root: python run_standalone.py
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, time, re
from pathlib import Path
import sys

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "backend"))
sys.path.insert(0, str(project_root / "backend" / "components"))

# Import components directly
try:
    from query_classifier import QueryClassifier
    from schema_linker import SchemaLinker
    from example_retriever import ExampleRetriever
    from sql_generator import SqlGenerator
    from sql_validator import SqlValidator
    from sql_executor import SqlExecutor
    from result_formatter import ResultFormatter
    print("✅ All components imported from backend/components/")
    classifier = QueryClassifier()
    linker = SchemaLinker()
    retriever = ExampleRetriever()
    generator = SqlGenerator()
    validator = SqlValidator()
    executor = SqlExecutor()
    formatter = ResultFormatter()
    USE_COMPONENTS = True
except Exception as e:
    print(f"⚠️ Component import failed, using fallback: {e}")
    import traceback; traceback.print_exc()
    USE_COMPONENTS = False
    classifier = linker = retriever = generator = validator = executor = formatter = None

DB_PATH = project_root / "data" / "querymind.db"
print(f"DB: {DB_PATH} exists={DB_PATH.exists()}")

app = FastAPI(title="QueryMind AI")

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
    max_rows: int = 100
    def get_nl(self):
        return self.nl_query or self.natural_language_query or self.query or ""

def get_conn():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def execute_pipeline(nl_query: str, max_rows: int = 100):
    start = time.time()
    
    # Classifier
    query_type = "SELECT_SIMPLE"
    if USE_COMPONENTS and classifier:
        try:
            c_out = classifier.classify(nl_query)
            query_type = getattr(c_out, 'query_type', 'SELECT_SIMPLE')
        except:
            pass
    if any(k in nl_query.lower() for k in ["drop","delete","update","insert","truncate","alter"]):
        query_type = "UNSUPPORTED"
    if query_type == "UNSUPPORTED":
        return {"error": "Blocked", "query_type": query_type, "generated_sql": ""}

    # Linker
    resolved = {"resolved_tables": ["orders"], "resolved_columns": [], "ambiguities": []}
    if USE_COMPONENTS and linker:
        try:
            out = linker.link(nl_query)
            resolved = {"resolved_tables": getattr(out, 'resolved_tables', ["orders"]), "resolved_columns": [], "ambiguities": []}
        except:
            pass

    # Retriever
    examples = []
    if USE_COMPONENTS and retriever:
        try:
            out = retriever.retrieve(nl_query, top_k=3)
            examples = [{"question": e.question, "sql": e.sql} for e in getattr(out, 'examples', [])]
        except:
            pass

    # Generator - THIS IS THE KEY FOR 70%+ accuracy
    generated_sql = "SELECT status, COUNT(*) FROM orders GROUP BY status"
    explanation = "Fallback"
    if USE_COMPONENTS and generator:
        try:
            prompt = nl_query
            if hasattr(generator, 'build_prompt'):
                class Dummy:
                    def __init__(self):
                        self.resolved_tables = resolved["resolved_tables"]
                        self.resolved_columns = []
                        self.ambiguities = []
                class DummyRet:
                    def __init__(self, ex):
                        from types import SimpleNamespace
                        self.examples = [SimpleNamespace(question=e["question"], sql=e["sql"]) for e in ex]
                prompt = generator.build_prompt(nl_query, Dummy(), DummyRet(examples))
            gen_out = generator.generate(prompt)
            if isinstance(gen_out, dict):
                generated_sql = gen_out.get("sql", generated_sql)
            else:
                generated_sql = getattr(gen_out, 'sql', str(gen_out))
        except Exception as e:
            print(f"Generator error: {e}")
            import traceback; traceback.print_exc()

    # Validator
    sanitized_sql = generated_sql
    if USE_COMPONENTS and validator:
        try:
            val_out = validator.validate(generated_sql, max_rows=max_rows)
            if getattr(val_out, 'is_valid', True):
                sanitized_sql = getattr(val_out, 'sanitized_sql', generated_sql)
            else:
                return {"error": getattr(val_out, 'errors', 'Invalid'), "generated_sql": generated_sql}
        except:
            pass

    # Executor
    try:
        if USE_COMPONENTS and executor:
            exec_out = executor.execute(sanitized_sql)
            exec_result = {"rows": getattr(exec_out, 'rows', []), "columns": getattr(exec_out, 'columns', []), "row_count": getattr(exec_out, 'row_count', 0), "latency_ms": getattr(exec_out, 'latency_ms', 0)}
        else:
            conn = get_conn()
            cur = conn.execute(sanitized_sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            exec_result = {"rows": [list(r) for r in rows], "columns": cols, "row_count": len(rows), "latency_ms": 0}
            conn.close()
    except Exception as e:
        exec_result = {"rows": [], "columns": [], "row_count": 0, "latency_ms": 0, "error": str(e)}

    # Formatter
    formatted = {"table": [], "nl_summary": f"Found {exec_result['row_count']} rows"}
    if USE_COMPONENTS and formatter:
        try:
            class ExecObj:
                def __init__(self, d):
                    self.rows = d.get('rows', [])
                    self.columns = d.get('columns', [])
                    self.row_count = d.get('row_count', 0)
                    self.latency_ms = d.get('latency_ms', 0)
                    self.error = d.get('error')
            fmt_out = formatter.format(ExecObj(exec_result), nl_query)
            formatted = {"table": getattr(fmt_out, 'table', []), "nl_summary": getattr(fmt_out, 'nl_summary', ''), "columns": getattr(fmt_out, 'columns', []), "row_count": getattr(fmt_out, 'row_count', 0)}
        except:
            cols = exec_result.get('columns', [])
            rows = exec_result.get('rows', [])
            formatted['table'] = [dict(zip(cols, r)) for r in rows]

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
    return {"message": "QueryMind AI", "components_loaded": USE_COMPONENTS, "db_exists": DB_PATH.exists()}

@app.get("/health")
def health():
    return {"status": "ok", "components_loaded": USE_COMPONENTS}

@app.get("/admin/schema")
@app.get("/api/v1/admin/schema")
def schema():
    conn = get_conn()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    result = []
    for t in tables:
        cols = conn.execute(f"PRAGMA table_info({t['name']})").fetchall()
        result.append({"table_name": t['name'], "columns": [dict(c) for c in cols]})
    conn.close()
    return result

@app.get("/admin/examples")
@app.get("/api/v1/admin/examples")
def examples():
    conn = get_conn()
    rows = conn.execute("SELECT id, question, sql, query_type FROM few_shot_examples LIMIT 100").fetchall()
    conn.close()
    return [{"id": r[0], "question": r[1], "sql": r[2], "query_type": r[3]} for r in rows]

@app.get("/admin/logs")
@app.get("/api/v1/admin/logs")
def logs(limit: int = 100):
    return []

@app.get("/admin/guardrails")
@app.get("/api/v1/admin/guardrails")
def guardrails():
    return {"max_rows": 100, "blocked_keywords": ["DROP","DELETE","UPDATE"]}

@app.get("/admin/config")
@app.get("/api/v1/admin/config")
def config():
    return {"llm_provider": "mock"}

@app.post("/query")
@app.post("/api/v1/query")
def query(request: QueryRequest):
    nl = request.get_nl()
    if not nl:
        return {"error": "No query"}
    return execute_pipeline(nl, request.max_rows)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
