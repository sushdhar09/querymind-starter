import csv
import io

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.main import app, store
from backend.schemas import ColumnMetaIn, Guardrails, ModelConfig
from backend.security import RateLimiter

KEY = {"X-API-Key": "test-admin-key"}
anon = TestClient(app)
admin = TestClient(app, headers=KEY)


@pytest.fixture(autouse=True)
def restore_state():
    yield
    store.set_guardrails(Guardrails())
    store.set_model_config(ModelConfig())
    store.upsert_column_metadata("customers", "email", ColumnMetaIn(description="Customer contact email address", synonyms=["e-mail", "email address"]))


ADMIN_ROUTES = [("GET", "/admin/schema"), ("GET", "/admin/examples"), ("GET", "/admin/logs"), ("GET", "/admin/logs/export"),
                ("GET", "/admin/stats"), ("GET", "/admin/guardrails"), ("GET", "/admin/config"), ("GET", "/admin/examples/search?q=x"),
                ("POST", "/admin/examples"), ("PUT", "/admin/examples/1"), ("DELETE", "/admin/examples/1"),
                ("PUT", "/admin/guardrails"), ("PUT", "/admin/config"), ("PUT", "/admin/schema/orders/status")]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
@pytest.mark.parametrize("prefix", ["", "/api/v1"])
def test_admin_routes_require_key(method, path, prefix):
    assert anon.request(method, prefix + path, json={}).status_code == 401
    assert anon.request(method, prefix + path, json={}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_bearer_token_accepted():
    assert anon.get("/admin/guardrails", headers={"Authorization": "Bearer test-admin-key"}).status_code == 200


def test_admin_disabled_without_server_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "")
    r = admin.get("/admin/guardrails")
    assert r.status_code == 503 and "ADMIN_API_KEY" in r.json()["detail"]


def test_public_endpoints_need_no_key():
    assert anon.get("/health").status_code == 200
    assert anon.post("/query", json={"nl_query": "Count orders by status"}).status_code == 200


def test_cors_allows_only_configured_origins():
    ok = anon.options("/query", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-credentials" not in ok.headers
    bad = anon.options("/query", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    from backend.security import limiter
    limiter._hits.clear()
    codes = [anon.post("/query", json={"nl_query": "Count orders by status"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = anon.post("/query", json={"nl_query": "Count orders by status"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    limiter._hits.clear()


def test_rate_limiter_window_and_keys():
    rl = RateLimiter(window=60)
    assert rl.check("a", 2, now=0) is None and rl.check("a", 2, now=1) is None
    assert rl.check("a", 2, now=2) == 59
    assert rl.check("b", 2, now=2) is None
    assert rl.check("a", 2, now=61) is None


def test_schema_includes_metadata_and_keys():
    tables = {t["table_name"]: t for t in admin.get("/admin/schema").json()}
    assert set(tables) == {"customers", "products", "orders", "order_items", "payments", "reviews"}
    status = next(c for c in tables["orders"]["columns"] if c["name"] == "status")
    assert status["type"] == "NVARCHAR" and status["description"] and not status["is_sensitive"]
    assert next(c for c in tables["orders"]["columns"] if c["name"] == "order_id")["primary_key"]


def test_update_column_metadata_persists_and_affects_pipeline():
    r = admin.put("/admin/schema/Customers/EMAIL", json={"description": "PII", "synonyms": [], "is_sensitive": True})
    assert r.status_code == 200 and r.json()["table_name"] == "customers" and r.json()["column_name"] == "email"
    col = next(c for t in admin.get("/admin/schema").json() if t["table_name"] == "customers" for c in t["columns"] if c["name"] == "email")
    assert col["is_sensitive"] and col["description"] == "PII"
    body = anon.post("/query", json={"nl_query": "Show customer email and city for customers who ordered in last 30 days"}).json()
    assert body["status"] == "blocked" and "Sensitive" in body["error"]


def test_update_unknown_column_404():
    assert admin.put("/admin/schema/orders/nope", json={}).status_code == 404
    assert admin.put("/admin/schema/nope/status", json={}).status_code == 404


def test_example_crud_roundtrip_and_validation():
    created = admin.post("/admin/examples", json={"question": "Zebra crossing orders", "sql": "SELECT status FROM orders WHERE status = 'pending'",
                                                   "query_type": "SELECT_SIMPLE"})
    assert created.status_code == 201
    ex = created.json()
    assert ex["id"] and ex["created_at"]
    assert any(e["id"] == ex["id"] for e in admin.get("/admin/examples?search=Zebra").json())
    upd = admin.put(f"/admin/examples/{ex['id']}", json={"question": "Zebra crossing orders v2", "sql": "SELECT order_id FROM orders", "query_type": "SELECT_SIMPLE"})
    assert upd.status_code == 200 and upd.json()["question"].endswith("v2") and upd.json()["sql"] == "SELECT order_id FROM orders"
    assert admin.delete(f"/admin/examples/{ex['id']}").status_code == 204
    assert admin.delete(f"/admin/examples/{ex['id']}").status_code == 404
    assert admin.put(f"/admin/examples/{ex['id']}", json={"question": "q", "sql": "SELECT 1"}).status_code == 404


@pytest.mark.parametrize("sql,fragment", [("DROP TABLE orders", "DROP statements are blocked"), ("SELECT nope FROM orders", "Unknown column"),
                                           ("SELECT * FROM missing", "Unknown table"), ("SELECT 1; DELETE FROM orders", "Exactly one")])
def test_bad_example_sql_rejected(sql, fragment):
    r = admin.post("/admin/examples", json={"question": "bad", "sql": sql})
    assert r.status_code == 422 and fragment in str(r.json()["detail"]["errors"])


def test_example_input_validation():
    assert admin.post("/admin/examples", json={"question": "", "sql": "SELECT 1"}).status_code == 422
    assert admin.post("/admin/examples", json={"question": "q", "sql": "SELECT status FROM orders", "query_type": "BOGUS"}).status_code == 422


def test_semantic_search_sees_new_example_immediately():
    ex = admin.post("/admin/examples", json={"question": "flamingo census by region", "sql": "SELECT city FROM customers"}).json()
    try:
        hits = admin.get("/admin/examples/search?q=flamingo census&top_k=3").json()
        assert hits[0]["question"] == "flamingo census by region" and hits[0]["score"] > 0.3
    finally:
        admin.delete(f"/admin/examples/{ex['id']}")
    assert all(h["question"] != "flamingo census by region" for h in admin.get("/admin/examples/search?q=flamingo census").json())


def test_guardrails_update_takes_effect_on_next_query():
    r = admin.put("/admin/guardrails", json={"max_rows": 5, "blocked_operations": ["DROP"], "allowed_table_prefixes": [], "blocked_tables": []})
    assert r.status_code == 200 and r.json()["max_rows"] == 5
    assert set(["query_logs", "few_shot_examples", "schema_descriptions"]) <= set(r.json()["blocked_tables"])
    assert admin.get("/admin/guardrails").json()["max_rows"] == 5
    body = anon.post("/query", json={"nl_query": "Orders in last 30 days"}).json()
    assert body["status"] == "success" and body["execution"]["row_count"] == 5 and body["generated_sql"].startswith("SELECT TOP 5 ")


def test_guardrail_table_scope_applies_live():
    admin.put("/admin/guardrails", json={"allowed_table_prefixes": ["order"]})
    assert anon.post("/query", json={"nl_query": "Count orders by status"}).json()["status"] == "success"
    body = anon.post("/query", json={"nl_query": "Show all customers from Mumbai"}).json()
    assert body["status"] == "blocked" and "outside the allowed scope" in body["error"]


@pytest.mark.parametrize("payload", [{"blocked_operations": ["EXPLODE"]}, {"max_rows": 0}, {"max_rows": 10**6}])
def test_guardrails_validation(payload):
    assert admin.put("/admin/guardrails", json=payload).status_code == 422


def test_config_update_and_key_check(monkeypatch):
    r = admin.put("/admin/config", json={"provider": "mock", "temperature": 0.2, "max_tokens": 512, "model": "m", "dialect": "tsql", "llm_summary": False})
    assert r.status_code == 200 and r.json()["temperature"] == 0.2 and r.json()["api_key_configured"] is True
    assert admin.get("/admin/config").json()["max_tokens"] == 512
    monkeypatch.setattr(main, "OPENAI_API_KEY", "")
    bad = admin.put("/admin/config", json={"provider": "openai"})
    assert bad.status_code == 422 and store.get_model_config().provider == "mock"
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-test")
    ok = admin.put("/admin/config", json={"provider": "openai"})
    assert ok.status_code == 200 and ok.json()["api_key_configured"] is True and "sk-test" not in ok.text


def test_config_rejects_dialects_other_than_tsql():
    for dialect in ("sqlite", "postgresql", "mysql"):
        assert admin.put("/admin/config", json={"provider": "mock", "dialect": dialect}).status_code == 422
    assert admin.put("/query", json={}).status_code in (404, 405)
    assert anon.post("/query", json={"nl_query": "Count orders by status", "dialect": "sqlite"}).status_code == 422


def test_config_validation():
    assert admin.put("/admin/config", json={"provider": "skynet"}).status_code == 422
    assert admin.put("/admin/config", json={"provider": "mock", "temperature": 5}).status_code == 422


def test_logs_filter_pagination_and_total_header():
    anon.post("/query", json={"nl_query": "Drop the customers table"})
    anon.post("/query", json={"nl_query": "Count orders by status"})
    r = admin.get("/admin/logs?status=blocked&search=Drop")
    assert r.status_code == 200 and r.json() and all(entry["status"] == "blocked" for entry in r.json())
    assert int(r.headers["x-total-count"]) >= len(r.json())
    page = admin.get("/admin/logs?limit=1&offset=1")
    assert len(page.json()) == 1 and int(page.headers["x-total-count"]) >= 2


def test_logs_csv_export_is_formula_safe():
    anon.post("/query", json={"nl_query": "=HYPERLINK(\"http://evil\",\"x\") show orders"})
    r = admin.get("/admin/logs/export?search=HYPERLINK")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows and rows[0]["nl_query"].startswith("'=HYPERLINK")
    assert list(rows[0]) == ["id", "created_at", "nl_query", "generated_sql", "status", "query_type", "failed_stage",
                             "latency_ms", "row_count", "error", "model", "request_id"]


def test_stats():
    anon.post("/query", json={"nl_query": "Count orders by status"})
    s = admin.get("/admin/stats").json()
    assert s["total"] >= 1 and s["by_status"].get("success", 0) >= 1 and 0 < s["success_rate"] <= 1 and s["avg_latency_ms"] >= 0
