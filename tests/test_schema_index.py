import json
from pathlib import Path

import pytest

from backend import demo_data
from backend.components.schema_linker import SchemaLinker
from backend.components.sql_validator import SqlValidator
from backend.db import DatabaseError
from backend.schema_index import SchemaIndex
from backend.schemas import ColumnMetaIn
from backend.store import AppStore
from backend.testing.inmemory_db import InMemoryDatabase

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "column_metadata.json"


def small_dataset(extra_customers=()):
    customers = [(1, "Asha", "a@x.com", "Mumbai", "2025-01-01", "d"), (2, "Ben", "b@x.com", "Delhi", "2025-01-02", "d"),
                 (3, "Cy", "c@x.com", "Mumbai", "2025-01-03", "d"), *extra_customers]
    return {"customers": customers,
            "orders": [(1, 1, "2026-01-01", 10.0, "pending", None), (2, 2, "2026-01-02", 20.0, "completed", None)],
            "payments": [(1, 1, 5.0, "paypal", None, "success"), (2, 2, 7.0, "net_banking", None, "refunded")]}


@pytest.fixture
def db():
    return InMemoryDatabase(small_dataset())


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db", metadata_seed=SEED).init()


@pytest.fixture
def index(db, store):
    return SchemaIndex(store, db)


def test_snapshot_lists_the_tables_and_indexes_low_cardinality_values(index):
    snap = index.snapshot()
    assert set(snap.columns) == {"customers", "products", "orders", "order_items", "payments", "reviews"}
    assert ("customers", "city", "Mumbai") in snap.values["mumbai"]
    assert ("payments", "payment_method", "net_banking") in snap.values["net banking"]
    assert snap.primary_keys["orders"] == {"order_id"} and ("orders", "customer_id", "customers", "customer_id") in snap.fks


def test_high_cardinality_columns_are_not_value_indexed(store):
    many = [(100 + i, f"n{i}", f"e{i}@x.com", f"City{i:03d}", "2025-01-01", "d") for i in range(60)]
    values = SchemaIndex(store, InMemoryDatabase(small_dataset(many))).snapshot().values
    assert "city001" not in values and "mumbai" not in values


def test_keys_dates_and_numeric_looking_values_are_never_indexed(index):
    values = index.snapshot().values
    assert not any(k.isdigit() for k in values) and not any(k.startswith("2025 01") for k in values)


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
    s = AppStore(tmp_path / "app.db", metadata_seed=seed).init()
    s.upsert_column_metadata("orders", "status", ColumnMetaIn(description="edited by admin"))
    s.init()
    assert s.list_column_metadata()[0].description == "edited by admin"


def test_real_seed_file_covers_every_column():
    snap = json.loads(SEED.read_text())
    for table in demo_data.SCHEMA:
        assert {c.name for c in table.columns} == set(snap[table.name]), table.name


class CountingDatabase:
    def __init__(self, inner):
        self.inner, self.schema_loads, self.fail = inner, 0, False
        self.dialect = inner.dialect

    def load_schema(self):
        self.schema_loads += 1
        if self.fail:
            raise DatabaseError("server unavailable")
        return self.inner.load_schema()

    def sample_values(self, table, column, limit):
        return self.inner.sample_values(table, column, limit)


def test_snapshot_is_cached_until_the_ttl_expires(store, db):
    counting, now = CountingDatabase(db), [0.0]
    index = SchemaIndex(store, counting, ttl_s=60, clock=lambda: now[0])
    index.snapshot()
    now[0] = 59
    index.snapshot()
    assert counting.schema_loads == 1
    now[0] = 61
    index.snapshot()
    assert counting.schema_loads == 2


def test_a_zero_ttl_reloads_every_time(store, db):
    counting = CountingDatabase(db)
    index = SchemaIndex(store, counting, ttl_s=0)
    index.snapshot()
    index.snapshot()
    assert counting.schema_loads == 2


def test_refresh_forces_a_reload(store, db):
    counting = CountingDatabase(db)
    index = SchemaIndex(store, counting, ttl_s=3600)
    index.snapshot()
    index.refresh()
    assert counting.schema_loads == 2


def test_a_failed_refresh_serves_the_previous_snapshot_and_retries_after_the_ttl(store, db):
    counting, now = CountingDatabase(db), [0.0]
    index = SchemaIndex(store, counting, ttl_s=60, clock=lambda: now[0])
    first = index.snapshot()
    counting.fail = True
    now[0] = 61
    assert index.snapshot() is first
    now[0] = 90
    assert index.snapshot() is first and counting.schema_loads == 2, "no retry before the next interval"
    counting.fail = False
    now[0] = 130
    assert index.snapshot() is not first and counting.schema_loads == 3


def test_the_first_load_failing_is_an_error(store, db):
    counting = CountingDatabase(db)
    counting.fail = True
    with pytest.raises(DatabaseError, match="server unavailable"):
        SchemaIndex(store, counting).snapshot()
    with pytest.raises(DatabaseError):
        SchemaIndex(store, counting).analyze("orders")


def test_a_column_that_cannot_be_sampled_is_skipped_not_fatal(store, db):
    class Flaky(CountingDatabase):
        def sample_values(self, table, column, limit):
            if (table, column) == ("customers", "city"):
                raise DatabaseError("timeout")
            return super().sample_values(table, column, limit)

    snap = SchemaIndex(store, Flaky(db)).snapshot()
    assert "mumbai" not in snap.values and "pending" in snap.values


def test_table_columns_feeds_the_validator_in_lowercase(index):
    cols = index.table_columns()
    assert cols["orders"] >= {"order_id", "total_amount", "status"} and set(cols) == set(demo_data.TABLE_NAMES)
    v = SqlValidator(schema=index.table_columns)
    assert v.validate("SELECT status FROM orders").valid and not v.validate("SELECT nope FROM orders").valid
