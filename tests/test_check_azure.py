import importlib.util
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from backend import demo_data
from backend.db import ColumnInfo, DatabaseError, ReadOnlyStatus
from backend.testing.inmemory_db import InMemoryDatabase

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("check_azure", ROOT / "data" / "check_azure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load()


class Recording(InMemoryDatabase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed = []

    def query(self, sql, max_rows, timeout_s):
        self.executed.append(sql)
        return super().query(sql, max_rows, timeout_s)


def run(database, argv=(), **kw):
    lines = []
    code = check.main(list(argv), database=database, out=lines.append, **kw)
    return code, "\n".join(lines)


def results_for(database):
    return check.run_checks(database, InMemoryDatabase())


def by_status(results, status):
    return [r for r in results if r.status == status]


def test_a_database_identical_to_the_demo_data_is_clean():
    results = results_for(InMemoryDatabase())
    assert not by_status(results, "WARN") and not by_status(results, "FAIL")
    assert len(results) > 70
    code, out = run(InMemoryDatabase())
    assert code == 0 and "0 warnings, 0 failures" in out


def test_different_data_gives_warnings_not_failures():
    other = InMemoryDatabase(demo_data.generate(seed=7, today=date.today()))
    results = results_for(other)
    assert not by_status(results, "FAIL") and len(by_status(results, "WARN")) > 10
    assert any("different values" in r.detail for r in by_status(results, "WARN"))
    assert run(other)[0] == 0
    assert run(other, ["--strict"])[0] == 1


def test_a_missing_column_fails_and_skips_the_query_checks():
    class NoStatus(InMemoryDatabase):
        def load_schema(self):
            schema = super().load_schema()
            schema.tables["orders"] = [c for c in schema.tables["orders"] if c.name != "status"]
            return schema

    results = results_for(NoStatus())
    assert any(r.status == "FAIL" and r.check == "column orders.status" for r in results)
    assert any(r.check == "queries" and "skipped" in r.detail for r in results)
    assert not any(r.check.startswith(("example", "template", "ground truth")) for r in results)
    assert run(NoStatus())[0] == 1


def test_a_missing_table_fails():
    class NoReviews(InMemoryDatabase):
        def load_schema(self):
            schema = super().load_schema()
            del schema.tables["reviews"]
            return schema

    assert any(r.status == "FAIL" and r.check == "table reviews" and r.detail == "missing" for r in results_for(NoReviews()))


def test_a_login_that_can_write_fails():
    class Writable(InMemoryDatabase):
        def read_only_status(self):
            return ReadOnlyStatus(False, "login can write: db_owner, INSERT permission")

    results = results_for(Writable())
    failure = next(r for r in by_status(results, "FAIL"))
    assert failure.check == "read-only login" and "db_owner" in failure.detail
    assert run(Writable())[0] == 1


def test_an_unreachable_server_fails_cleanly_and_runs_nothing_else():
    class Down(InMemoryDatabase):
        def ping(self):
            raise DatabaseError("Could not connect to Azure SQL (08001)")

    results = results_for(Down())
    assert [(r.status, r.check) for r in results] == [("FAIL", "connection")] and "08001" in results[0].detail
    assert run(Down())[0] == 1


def test_a_failing_permission_probe_is_a_failure_not_a_crash():
    class Broken(InMemoryDatabase):
        def read_only_status(self):
            raise DatabaseError("permission denied on HAS_PERMS_BY_NAME")

    results = results_for(Broken())
    assert any(r.status == "FAIL" and r.check == "read-only login" and "could not inspect" in r.detail for r in results)


def test_queries_that_error_on_the_server_are_failures_with_the_server_message():
    class NoDateDiff(InMemoryDatabase):
        def query(self, sql, max_rows, timeout_s):
            if "DATEDIFF" in sql:
                raise DatabaseError("Invalid usage of the option DAY in the DATEDIFF function.")
            return super().query(sql, max_rows, timeout_s)

    results = results_for(NoDateDiff())
    failed = by_status(results, "FAIL")
    assert failed and all("does not run on Azure" in r.detail and "DATEDIFF" in r.detail for r in failed)
    assert len(failed) < 6 and any(r.status == "OK" and r.check.startswith("example") for r in results)


def test_a_schema_difference_in_types_extra_columns_and_foreign_keys_is_a_warning():
    class Drifted(InMemoryDatabase):
        def load_schema(self):
            schema = super().load_schema()
            schema.tables["products"] = [ColumnInfo(c.name, "REAL" if c.name == "price" else c.type) for c in schema.tables["products"]] + [ColumnInfo("sku", "NVARCHAR")]
            schema.foreign_keys = [fk for fk in schema.foreign_keys if fk[0] != "reviews"]
            return schema

    warnings = {r.check: r.detail for r in by_status(results_for(Drifted()), "WARN")}
    assert "type is REAL, expected FLOAT" in warnings["column products.price"]
    assert "sku" in warnings["table products"]
    assert "reviews.product_id->products.product_id" in warnings["foreign keys"]


def test_row_counts_and_categories_that_differ_are_reported():
    rows = demo_data.generate(today=date.today())
    rows["orders"] = [(*r[:4], "shipped", r[5]) if i % 2 else r for i, r in enumerate(rows["orders"])][:4000]
    warnings = {r.check: r.detail for r in by_status(results_for(InMemoryDatabase(rows)), "WARN")}
    assert "4000 rows, the demo dataset has 5000" in warnings["row count orders"]
    assert "shipped" in warnings["values orders.status"]


def test_the_check_only_ever_reads():
    db = Recording()
    results_for(db)
    assert db.executed and all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in db.executed)


@pytest.mark.parametrize("sql,level", [
    ("SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -30, CAST(GETDATE() AS DATE))", "none"),
    ("SELECT YEAR(signup_date) AS y, COUNT(*) AS n FROM customers GROUP BY YEAR(signup_date)", "none"),
    ("SELECT FORMAT(order_date, 'yyyy-MM') AS m, COUNT(*) AS n FROM orders GROUP BY FORMAT(order_date, 'yyyy-MM')", "none"),
    ("SELECT status, COUNT(*) AS n FROM orders GROUP BY order_date, status", "none"),
    ("SELECT * FROM customers WHERE signup_date IS NOT NULL AND city = 'x' AND signup_date > '2020-01-01'", "none"),
    ("SELECT * FROM customers WHERE city = 'Mumbai'", "shape"),
    ("SELECT signup_date FROM customers", "shape"),
    ("SELECT o.* FROM orders o JOIN payments p ON o.order_id = p.order_id", "shape"),
    ("SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status", "full"),
    ("SELECT name FROM customers ORDER BY name", "full"),
    ("SELECT COUNT(*) AS n FROM orders", "full"),
    ("SELECT COUNT(*) AS n, MAX(total_amount) AS m FROM orders WHERE status = 'pending'", "full"),
    ("SELECT * FROM orders", "shape"),
    ("SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.category", "full"),
])
def test_comparison_level(sql, level):
    assert check.comparison_level(sql) == level


def test_shipped_queries_are_deduplicated_and_cover_every_source():
    from backend.components.sql_generator import SqlGenerator
    queries = check.shipped_queries()
    sqls = {sql for _, sql in queries}
    assert len(sqls) == len(queries) >= 60
    assert {label.split(":")[0] for label, _ in queries} >= {"example", "ground truth"}
    assert set(SqlGenerator().templates.values()) <= sqls, "every mock template must be checked, even when it duplicates another query"


def test_main_requires_the_connection_string(monkeypatch, tmp_path):
    monkeypatch.delenv("AZURE_SQL_CONNECTION_STRING", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    lines = []
    assert check.main([], out=lines.append) == 1 and "AZURE_SQL_CONNECTION_STRING is not set" in lines[0]


def test_json_report_and_summary(tmp_path):
    target = tmp_path / "report.json"
    code, out = run(InMemoryDatabase(), ["--json", str(target)])
    data = json.loads(target.read_text(encoding="utf-8"))
    assert code == 0 and all({"status", "check", "detail"} <= set(r) for r in data) and out.rstrip().endswith("0 failures")
    assert replace(check.Result("OK", "x"), detail="y").detail == "y"


def test_importing_the_script_does_not_require_pyodbc():
    import subprocess
    import sys
    code = ("import sys; sys.modules['pyodbc'] = None; import importlib.util as u; "
            "s = u.spec_from_file_location('s', 'data/check_azure.py'); m = u.module_from_spec(s); s.loader.exec_module(m); print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.stdout.strip() == "ok", out.stderr
