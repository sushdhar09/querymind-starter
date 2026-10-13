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
