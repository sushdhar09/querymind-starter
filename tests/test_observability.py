import json
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.config import DB_PATH
from backend.main import app, get_pipeline
from backend.observability import JsonFormatter, RequestIdFilter, configure_logging, request_id_var, sanitize_request_id
from backend.store import MIGRATIONS, AppStore

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run data/seed_db.py first")
client = TestClient(app)
admin = TestClient(app, headers={"X-API-Key": "test-admin-key"})


def test_every_response_has_a_request_id():
    rid = client.get("/health").headers["x-request-id"]
    assert len(rid) == 16 and rid != client.get("/health").headers["x-request-id"]


def test_valid_incoming_request_id_is_echoed():
    assert client.get("/health", headers={"X-Request-ID": "trace-abc.123_X"}).headers["x-request-id"] == "trace-abc.123_X"


@pytest.mark.parametrize("bad", ["x" * 65, "has space", "semi;colon", "new\tline", "<script>"])
def test_unsafe_incoming_request_id_is_replaced(bad):
    got = client.get("/health", headers={"X-Request-ID": bad}).headers["x-request-id"]
    assert got != bad and sanitize_request_id(got) == got


def test_sanitize_request_id():
    assert sanitize_request_id("ok-1") == "ok-1" and sanitize_request_id("") == "" and sanitize_request_id("a b") == ""


def test_request_id_header_is_exposed_to_browsers():
    r = client.options("/query", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-request-id" in client.get("/health", headers={"Origin": "http://localhost:3000"}).headers["access-control-expose-headers"].lower()


def test_access_log_has_structured_fields_and_no_secrets(caplog):
    caplog.set_level(logging.INFO)
    admin.get("/admin/logs?search=secret-search-term", headers={"X-Request-ID": "req-1"})
    records = [r for r in caplog.records if r.name == "querymind.access"]
    assert len(records) == 1
    r = records[0]
    assert (r.method, r.path, r.status) == ("GET", "/admin/logs", 200) and r.duration_ms >= 0
    assert r.request_id == "req-1"
    everything = " ".join(str(v) for rec in caplog.records if rec.name.startswith("querymind") for v in rec.__dict__.values())
    assert "test-admin-key" not in everything and "secret-search-term" not in everything


def test_query_is_logged_with_request_id_but_not_the_question(caplog):
    caplog.set_level(logging.INFO)
    q = "Count orders by status"
    resp = client.post("/query", json={"nl_query": q}, headers={"X-Request-ID": "corr-42"})
    assert resp.headers["x-request-id"] == "corr-42"
    rec = next(r for r in caplog.records if r.name == "querymind.query")
    assert rec.status == "success" and rec.provider == "mock" and rec.question_chars == len(q) and rec.latency_ms >= 0
    assert q not in " ".join(str(v) for r in caplog.records if r.name.startswith("querymind") for v in r.__dict__.values())
    row = next(l for l in admin.get("/admin/logs?limit=20").json() if l["request_id"] == "corr-42")
    assert row["nl_query"] == q and row["status"] == "success"


def test_csv_export_includes_request_id():
    client.post("/query", json={"nl_query": "Count orders by status"}, headers={"X-Request-ID": "csv-77"})
    assert "request_id" in admin.get("/admin/logs/export").text.splitlines()[0]
    assert "csv-77" in admin.get("/admin/logs/export?search=Count").text


def test_json_formatter_output():
    record = logging.LogRecord("n", logging.WARNING, "f.py", 1, "hello %s", ("world",), None)
    record.request_id, record.latency_ms = "r1", 12
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "hello world" and out["level"] == "WARNING" and out["request_id"] == "r1" and out["latency_ms"] == 12
    assert out["ts"].endswith("+00:00") and "exc" not in out


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord("n", logging.ERROR, "f.py", 1, "failed", (), sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc"]


def test_filter_keeps_explicit_request_id_and_falls_back_to_context():
    f = RequestIdFilter()
    explicit = logging.LogRecord("n", logging.INFO, "f.py", 1, "m", (), None)
    explicit.request_id = "given"
    f.filter(explicit)
    token = request_id_var.set("ctx-9")
    implicit = logging.LogRecord("n", logging.INFO, "f.py", 1, "m", (), None)
    f.filter(implicit)
    request_id_var.reset(token)
    assert explicit.request_id == "given" and implicit.request_id == "ctx-9"


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    before = len([h for h in root.handlers if getattr(h, "_querymind", False)])
    configure_logging("json", "INFO")
    configure_logging("json", "INFO")
    handlers = [h for h in root.handlers if getattr(h, "_querymind", False)]
    assert before == 1 and len(handlers) == 1 and isinstance(handlers[0].formatter, JsonFormatter)
    configure_logging("text", "INFO")
    assert not isinstance([h for h in root.handlers if getattr(h, "_querymind", False)][0].formatter, JsonFormatter)


def test_unhandled_error_returns_generic_body_with_request_id(caplog):
    class Exploding:
        def run(self, req):
            raise RuntimeError("secret internals /etc/passwd")

    app.dependency_overrides[get_pipeline] = lambda: Exploding()
    try:
        crash_client = TestClient(app, raise_server_exceptions=False)
        r = crash_client.post("/query", json={"nl_query": "Count orders"}, headers={"X-Request-ID": "crash-1"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error", "request_id": "crash-1"}
    assert r.headers["x-request-id"] == "crash-1" and "secret internals" not in r.text
    err = next(rec for rec in caplog.records if rec.name == "querymind.error")
    assert err.request_id == "crash-1" and "secret internals" in str(err.exc_info[1])


def test_health_is_cheap_and_ready_reports_checks():
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["checks"] == {"analytics_db": True, "app_db": True}
    assert body["llm_provider"] == "mock" and body["llm_api_key_configured"] is True


def test_ready_fails_when_analytics_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "missing.db")
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready" and r.json()["checks"]["analytics_db"] is False


def test_migration_adds_request_id_and_keeps_existing_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO query_logs (nl_query, status) VALUES ('legacy question', 'success')")
    conn.commit()
    conn.close()
    store = AppStore(path, None).init()
    with store._conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) >= 2
    [rec] = store.list_logs()
    assert rec.nl_query == "legacy question" and rec.request_id is None
    store.init()
