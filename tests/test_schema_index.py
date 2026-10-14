import json
import sqlite3
from pathlib import Path

import pytest

from backend.components.schema_linker import SchemaLinker
from backend.components.sql_validator import SqlValidator
from backend.schema_index import SchemaIndex
from backend.schemas import ColumnMetaIn
from backend.store import AppStore

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "column_metadata.json"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript((ROOT / "data" / "schema.sql").read_text())
    conn.executemany("INSERT INTO customers (customer_id, name, email, city) VALUES (?,?,?,?)",
                     [(1, "Asha", "a@x.com", "Mumbai"), (2, "Ben", "b@x.com", "Delhi"), (3, "Cy", "c@x.com", "Mumbai")])
    conn.executemany("INSERT INTO orders (order_id, customer_id, order_date, status) VALUES (?,?,?,?)",
                     [(1, 1, "2026-01-01", "pending"), (2, 2, "2026-01-02", "completed")])
    conn.executemany("INSERT INTO payments (payment_id, order_id, amount, payment_method, status) VALUES (?,?,?,?,?)",
                     [(1, 1, 5, "paypal", "success"), (2, 2, 7, "net_banking", "refunded")])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db", tmp_path / "none.db", metadata_seed=SEED).init()


@pytest.fixture
def index(db, store):
    return SchemaIndex(store, db)


def test_snapshot_excludes_internal_tables_and_indexes_low_cardinality_values(index):
    snap = index.snapshot()
    assert set(snap.columns) == {"customers", "products", "orders", "order_items", "payments", "reviews"}
    assert ("customers", "city", "Mumbai") in snap.values["mumbai"]
    assert ("payments", "payment_method", "net_banking") in snap.values["net banking"]


def test_high_cardinality_columns_are_not_value_indexed(db, store):
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO customers (customer_id, name, email, city) VALUES (?,?,?,?)",
                     [(100 + i, f"n{i}", f"e{i}@x.com", f"City{i:03d}") for i in range(60)])
    conn.commit()
    conn.close()
    values = SchemaIndex(store, db).snapshot().values
    assert "city001" not in values and "mumbai" not in values


def test_value_hits_resolve_columns(index):
    a = index.analyze("Show all customers from Mumbai")
    assert a.tables == ["customers"] and a.columns == ["customers.city"] and a.value_hits == ["customers.city = 'Mumbai'"]


def test_plural_and_singular_table_names(index):
    assert index.analyze("how many review rows").tables == ["reviews"]
    assert index.analyze("payments").tables == ["payments"]


def test_synonyms_from_metadata(index):
    assert "payments.amount" in index.analyze("total collected").columns
    assert "customers.signup_date" in index.analyze("customers who joined").columns


def test_revenue_is_ambiguous_without_table_context(index):
    a = index.analyze("show revenue by month")
    assert [x.term for x in a.ambiguities] == ["revenue"]
    assert set(a.ambiguities[0].candidates) == {"orders.total_amount", "payments.amount"}
    assert "`orders.total_amount`" in a.ambiguities[0].message and a.columns == []


def test_ambiguity_resolved_by_mentioned_table(index):
    a = index.analyze("total revenue from payments")
    assert not a.ambiguities and "payments.amount" in a.columns
    b = index.analyze("orders revenue")
    assert not b.ambiguities and "orders.total_amount" in b.columns


def test_ambiguity_resolved_by_user_choice(index):
    a = index.analyze("show revenue by month", {"revenue": "payments.amount"})
    assert not a.ambiguities and a.columns == ["payments.amount"]
    bogus = index.analyze("show revenue by month", {"revenue": "customers.city"})
    assert [x.term for x in bogus.ambiguities] == ["revenue"]


def test_shared_column_name_narrowed_by_table(index):
    assert [x.term for x in index.analyze("count by status").ambiguities] == ["status"]
    a = index.analyze("count orders by status")
    assert not a.ambiguities and "orders.status" in a.columns


def test_metadata_edits_take_effect_immediately(index, store):
    assert "orders.shipping_date" not in index.analyze("dispatched").columns
    store.upsert_column_metadata("orders", "shipping_date", ColumnMetaIn(description="x", synonyms=["dispatched"]))
    assert "orders.shipping_date" in index.analyze("dispatched").columns


def test_sensitive_columns_excluded_from_context_and_columns(index, store):
    store.upsert_column_metadata("customers", "email", ColumnMetaIn(description="PII", is_sensitive=True))
    a = index.analyze("show customer email and city")
    assert "customers.email" in a.excluded and "customers.email" not in a.columns and "customers.city" in a.columns
    out = SchemaLinker(index).link("show customer email and city")
    customers = next(t for t in out.schema_context if t.table == "customers")
    assert "email" not in {c.name for c in customers.columns} and out.excluded_columns == ["customers.email"]


def test_sensitive_column_values_are_not_indexed(index, store):
    store.upsert_column_metadata("customers", "city", ColumnMetaIn(is_sensitive=True))
    assert index.analyze("customers from Mumbai").value_hits == []


def test_validator_blocks_sensitive_columns(db, store):
    store.upsert_column_metadata("customers", "email", ColumnMetaIn(is_sensitive=True))
    schema = {"customers": {"customer_id", "name", "email", "city"}, "orders": {"order_id", "customer_id"}}
    v = SqlValidator(schema=schema, blocked_columns=store.sensitive_columns)
    for sql in ["SELECT email FROM customers", "SELECT c.email FROM customers c", "SELECT * FROM customers",
                "SELECT c.* FROM customers c JOIN orders o ON o.customer_id = c.customer_id",
                "SELECT name FROM customers WHERE email = 'a@x.com'"]:
        out = v.validate(sql)
        assert not out.valid and "Sensitive" in out.errors[0], sql
    for sql in ["SELECT name, city FROM customers", "SELECT COUNT(*) FROM customers", "SELECT o.* FROM orders o"]:
        assert v.validate(sql).valid, sql


def test_validator_sensitive_check_disabled_when_nothing_flagged(store):
    v = SqlValidator(schema={"customers": {"email"}}, blocked_columns=store.sensitive_columns)
    assert v.validate("SELECT * FROM customers").valid


def test_seed_does_not_overwrite_admin_edits(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"orders": {"status": {"description": "seeded"}}}))
    s = AppStore(tmp_path / "app.db", tmp_path / "none.db", metadata_seed=seed).init()
    s.upsert_column_metadata("orders", "status", ColumnMetaIn(description="edited by admin"))
    s.init()
    assert s.list_column_metadata()[0].description == "edited by admin"


def test_real_seed_file_covers_every_column():
    snap = json.loads(SEED.read_text())
    conn = sqlite3.connect(ROOT / "data" / "querymind.db") if (ROOT / "data" / "querymind.db").exists() else None
    if conn is None:
        pytest.skip("run data/seed_db.py first")
    for table in ["customers", "products", "orders", "order_items", "payments", "reviews"]:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        assert cols == set(snap[table]), table
