from datetime import date, timedelta

import sqlglot
from sqlglot import exp

from backend import demo_data as d

TODAY = date(2026, 10, 13)


def test_row_counts_match_the_documented_sizes():
    rows = d.generate(today=TODAY)
    assert {k: len(v) for k, v in rows.items()} == d.ROW_COUNTS == {
        "customers": 1000, "products": 200, "orders": 5000, "order_items": 15000, "payments": 5000, "reviews": 3000}
    assert tuple(rows) == d.TABLE_NAMES


def test_generation_is_deterministic_and_seed_sensitive():
    assert d.generate(seed=42, today=TODAY) == d.generate(seed=42, today=TODAY)
    assert d.generate(seed=1, today=TODAY) != d.generate(seed=2, today=TODAY)


def test_dates_are_relative_to_the_reference_day():
    rows = d.generate(today=TODAY)
    order_dates = [r[2] for r in rows["orders"]]
    assert max(order_dates) <= TODAY and min(order_dates) >= TODAY - timedelta(days=365)
    later = d.generate(today=TODAY + timedelta(days=10))
    assert max(r[2] for r in later["orders"]) > max(order_dates) - timedelta(days=1)


def test_foreign_keys_and_value_ranges_hold():
    rows = d.generate(today=TODAY)
    ids = {t: {r[0] for r in rows[t]} for t in rows}
    for table in d.SCHEMA:
        for i, col in enumerate(table.columns):
            if col.references:
                ref_table, _ = col.references.split(".")
                assert {r[i] for r in rows[table.name]} <= ids[ref_table], f"{table.name}.{col.name}"
    assert all(1 <= r[3] <= 5 for r in rows["reviews"])
    price_of = {r[0]: r[3] for r in rows["products"]}
    assert all(r[4] == price_of[r[2]] for r in rows["order_items"])
    assert all(r[5] > r[2] for r in rows["orders"]), "shipping must follow the order date"
    assert {r[4] for r in rows["orders"]} == set(d.ORDER_STATUSES)


def test_payment_amounts_follow_order_totals_with_some_refund_discounts():
    rows = d.generate(today=TODAY)
    totals = {r[0]: r[3] for r in rows["orders"]}
    diffs = [p for p in rows["payments"] if p[2] != totals[p[1]]]
    assert 0.05 < len(diffs) / len(rows["payments"]) < 0.15
    assert all(abs(p[2] - totals[p[1]] * 0.9) < 0.011 for p in diffs)


def test_every_column_in_the_rows_matches_the_schema_width():
    rows = d.generate(today=TODAY)
    for table in d.SCHEMA:
        assert all(len(r) == len(table.columns) for r in rows[table.name])


def test_tsql_ddl_parses_and_describes_every_table():
    statements = [s for s in sqlglot.parse(d.tsql_ddl(), read="tsql") if s is not None]
    creates = [s for s in statements if isinstance(s, exp.Create)]
    drops = [s for s in statements if isinstance(s, exp.Drop)]
    assert len(creates) == len(drops) == len(d.SCHEMA)
    ddl = d.tsql_ddl()
    for table in d.SCHEMA:
        assert f"CREATE TABLE dbo.{table.name}" in ddl and f"CONSTRAINT pk_{table.name} PRIMARY KEY" in ddl
        for col in table.columns:
            if col.references:
                assert f"FOREIGN KEY ({col.name}) REFERENCES dbo.{col.references.split('.')[0]}" in ddl
    assert "CHECK (rating BETWEEN 1 AND 5)" in ddl
    dropped = [t.name for s in drops for t in s.find_all(exp.Table)]
    assert dropped == list(reversed(d.TABLE_NAMES)), "drop children before parents"


def test_base_type():
    assert d.base_type("NVARCHAR(200)") == "NVARCHAR" and d.base_type("nvarchar(max)") == "NVARCHAR" and d.base_type("INT") == "INT"
