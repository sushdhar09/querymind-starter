import sqlite3

import pytest

from backend.schemas import ColumnMetaIn, ExampleIn, Guardrails, LogIn, ModelConfig
from backend.store import MIGRATIONS, AppStore

SCHEMA_SQL = """
CREATE TABLE few_shot_examples (id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, sql TEXT, query_type TEXT);
CREATE TABLE schema_descriptions (table_name TEXT, column_name TEXT, description TEXT, is_sensitive BOOLEAN, synonyms TEXT);
INSERT INTO few_shot_examples (question, sql, query_type) VALUES ('a', 'SELECT 1', 'SELECT_SIMPLE'), ('a', 'SELECT 1', 'SELECT_SIMPLE'), ('b', 'SELECT 2', 'SELECT_AGGREGATE');
INSERT INTO schema_descriptions VALUES ('customers', 'email', 'Contact email', 1, 'mail, e-mail');
"""


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db", tmp_path / "missing.db").init()


def test_init_is_idempotent_and_versioned(tmp_path):
    s = AppStore(tmp_path / "app.db", tmp_path / "missing.db")
    s.init().init()
    with s._conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_example_crud(store):
    ex = store.create_example(ExampleIn(question="top customers", sql="SELECT 1", query_type="SELECT_AGGREGATE"))
    assert ex.id and store.get_example(ex.id).question == "top customers"
    updated = store.update_example(ex.id, ExampleIn(question="top 5 customers", sql="SELECT 2"))
    assert updated.question == "top 5 customers" and updated.query_type == "SELECT_SIMPLE"
    assert [e.id for e in store.list_examples(search="top 5")] == [ex.id]
    assert store.list_examples(query_type="SELECT_AGGREGATE") == []
    assert store.delete_example(ex.id) and not store.delete_example(ex.id)
    assert store.get_example(ex.id) is None and store.update_example(ex.id, ExampleIn(question="x", sql="y")) is None


def test_example_validation():
    with pytest.raises(ValueError):
        ExampleIn(question="", sql="SELECT 1")
    with pytest.raises(ValueError):
        ExampleIn(question="q", sql="SELECT 1", query_type="BOGUS")


def test_column_metadata_upsert(store):
    store.upsert_column_metadata("orders", "total_amount", ColumnMetaIn(description="Order total", synonyms=["revenue"]))
    store.upsert_column_metadata("orders", "total_amount", ColumnMetaIn(description="Gross total", is_sensitive=True))
    [m] = store.list_column_metadata()
    assert m.description == "Gross total" and m.is_sensitive and m.synonyms == []


def test_logs_filter_and_order(store):
    store.add_log(LogIn(nl_query="count orders", generated_sql="SELECT 1", status="success", row_count=1))
    store.add_log(LogIn(nl_query="drop it", status="blocked", failed_stage="classifier", error="nope"))
    assert [l.nl_query for l in store.list_logs()] == ["drop it", "count orders"]
    assert [l.status for l in store.list_logs(status="blocked")] == ["blocked"]
    assert [l.nl_query for l in store.list_logs(search="count")] == ["count orders"]
    assert len(store.list_logs(limit=1, offset=1)) == 1


def test_settings_defaults_and_persistence(store, tmp_path):
    assert store.get_guardrails() == Guardrails()
    assert store.get_model_config().provider == "mock"
    store.set_guardrails(Guardrails(max_rows=25, blocked_operations=["DROP"]))
    store.set_model_config(ModelConfig(provider="openai", temperature=0.2))
    reopened = AppStore(tmp_path / "app.db", tmp_path / "missing.db").init()
    assert reopened.get_guardrails().max_rows == 25 and reopened.get_model_config().provider == "openai"


def test_legacy_import_dedupes_and_runs_once(tmp_path):
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    s = AppStore(tmp_path / "app.db", legacy).init()
    assert [e.question for e in s.list_examples()] == ["a", "b"]
    [m] = s.list_column_metadata()
    assert m.column_name == "email" and m.is_sensitive and m.synonyms == ["mail", "e-mail"]
    s.delete_example(s.list_examples()[0].id)
    s.init()
    assert len(s.list_examples()) == 1
