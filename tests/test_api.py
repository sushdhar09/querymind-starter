import pytest
from fastapi.testclient import TestClient

from backend.config import DB_PATH
from backend.main import app

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run data/seed_db.py first")
client = TestClient(app)


@pytest.mark.parametrize("prefix", ["", "/api/v1"])
def test_query_both_prefixes(prefix):
    r = client.post(f"{prefix}/query", json={"nl_query": "Count orders by status"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "success" and body["execution"]["row_count"] > 0


def test_destructive_request_blocked():
    body = client.post("/query", json={"nl_query": "Drop the customers table"}).json()
    assert body["status"] == "blocked" and body["generated_sql"] == ""


def test_unknown_question_errors_instead_of_default_query():
    body = client.post("/query", json={"nl_query": "what is the meaning of life"}).json()
    assert body["status"] == "error" and body["failed_stage"] == "generator"


def test_request_validation():
    assert client.post("/query", json={"nl_query": ""}).status_code == 422
    assert client.post("/query", json={"nl_query": "x", "max_rows": 0}).status_code == 422


def test_admin_endpoints():
    for path in ["schema", "examples", "logs", "guardrails", "config"]:
        assert client.get(f"/api/v1/admin/{path}").status_code == 200


def test_query_is_logged_and_visible_in_admin_logs():
    client.post("/query", json={"nl_query": "Count orders by status"})
    client.post("/query", json={"nl_query": "Drop the customers table"})
    logs = client.get("/api/v1/admin/logs?limit=5").json()
    statuses = {l["nl_query"]: l["status"] for l in logs}
    assert statuses["Count orders by status"] == "success" and statuses["Drop the customers table"] == "blocked"


def test_admin_examples_and_settings_come_from_store():
    assert len(client.get("/admin/examples").json()) > 0
    assert client.get("/admin/guardrails").json()["max_rows"] == 100
    assert client.get("/admin/config").json()["provider"] == "mock"


def test_ambiguity_surfaced_and_clarification_flow():
    body = client.post("/query", json={"nl_query": "show revenue by month"}).json()
    assert [a["term"] for a in body["resolved_entities"]["ambiguities"]] == ["revenue"]
    asked = client.post("/query", json={"nl_query": "show revenue by month", "clarify_ambiguities": True}).json()
    assert asked["status"] == "needs_clarification" and "orders.total_amount" in asked["error"]
    answered = client.post("/query", json={"nl_query": "show revenue by month", "clarify_ambiguities": True,
                                           "column_choices": {"revenue": "orders.total_amount"}}).json()
    assert answered["status"] == "success" and not answered["resolved_entities"]["ambiguities"]


def test_resolved_entities_include_schema_context_and_values():
    body = client.post("/query", json={"nl_query": "Show all customers from Mumbai"}).json()
    ents = body["resolved_entities"]
    assert ents["resolved_tables"] == ["customers"] and ents["value_hits"] == ["customers.city = 'Mumbai'"]
    assert ents["schema_context"][0]["table"] == "customers"
    assert any(c["description"] for c in ents["schema_context"][0]["columns"])


def test_sensitive_column_toggle_end_to_end():
    from backend.main import store
    from backend.schemas import ColumnMetaIn
    q = {"nl_query": "Show customer email and city for customers who ordered in last 30 days"}
    assert client.post("/query", json=q).json()["status"] == "success"
    store.upsert_column_metadata("customers", "email", ColumnMetaIn(description="PII", is_sensitive=True))
    try:
        body = client.post("/query", json=q).json()
        assert body["status"] == "blocked" and body["failed_stage"] == "validator" and "Sensitive" in body["error"]
        assert "customers.email" in body["resolved_entities"]["excluded_columns"]
    finally:
        store.upsert_column_metadata("customers", "email", ColumnMetaIn(description="PII", is_sensitive=False))
