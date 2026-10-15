import importlib.util
import sys
from pathlib import Path

import pytest

from backend import demo_data

ROOT = Path(__file__).resolve().parent.parent
SECRET = "Pwd=top-secret-admin-password"
ENV = {"AZURE_SQL_ADMIN_CONNECTION_STRING": f"Driver=x;Server=y;Uid=admin;{SECRET};"}


def load():
    spec = importlib.util.spec_from_file_location("seed_azure", ROOT / "data" / "seed_azure.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["seed_azure"] = module
    spec.loader.exec_module(module)
    return module


seed_azure = load()


class Cursor:
    def __init__(self, conn):
        self.conn, self._rows, self.fast_executemany = conn, [], False

    def execute(self, sql):
        self.conn.log.append(("execute", sql))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError(f"boom {SECRET}")
        if "FROM sys.tables" in sql:
            self._rows = [(t,) for t in self.conn.existing]
        elif sql.startswith("SELECT COUNT_BIG"):
            table = sql.split("dbo.[")[1].rstrip("]")
            self._rows = [(self.conn.existing[table],)]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]

    def executemany(self, sql, batch):
        self.conn.log.append(("executemany", sql, len(batch)))


class Conn:
    def __init__(self, existing=None, fail_on=None):
        self.existing, self.fail_on, self.log = existing or {}, fail_on, []
        self.committed = self.rolled_back = self.closed = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1


def run(argv, conn=None, env=ENV):
    out, connects = [], []
    conn = conn or Conn()

    def connect(cs, timeout):
        connects.append(cs)
        return conn

    code = seed_azure.main(argv, connect=connect, env=env, out=out.append)
    return code, "\n".join(out), conn, connects


def test_committed_schema_file_is_generated_from_the_dataset_definition():
    assert (ROOT / "data" / "schema.sql").read_text(encoding="utf-8") == demo_data.tsql_ddl()


def test_write_schema_needs_no_database(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_azure, "SCHEMA_FILE", tmp_path / "schema.sql")
    code, out, _, connects = run(["--write-schema"], env={})
    assert code == 0 and not connects and (tmp_path / "schema.sql").read_text(encoding="utf-8") == demo_data.tsql_ddl()


def test_without_yes_it_is_a_dry_run_and_never_connects():
    code, out, conn, connects = run([])
    assert code == 2 and "Dry run" in out and "Re-run with --yes" in out and not connects


def test_missing_admin_connection_string_is_a_clear_error_without_connecting():
    code, out, _, connects = run(["--yes"], env={})
    assert code == 1 and "AZURE_SQL_ADMIN_CONNECTION_STRING is not set" in out and not connects


def test_fresh_database_gets_tables_in_dependency_order_and_all_rows_in_one_transaction():
    code, out, conn, _ = run(["--yes"])
    assert code == 0 and conn.committed == 1 and conn.rolled_back == 0 and conn.closed == 1
    statements = [e[1] for e in conn.log if e[0] == "execute" and e[1].startswith("CREATE")]
    assert [s.split()[2] for s in statements] == [f"dbo.{t}" for t in demo_data.TABLE_NAMES]
    assert not any(e[1].startswith("DROP") for e in conn.log if e[0] == "execute")
    loaded = {}
    for e in conn.log:
        if e[0] == "executemany":
            table = e[1].split("INSERT INTO dbo.")[1].split(" ")[0]
            loaded[table] = loaded.get(table, 0) + e[2]
    assert loaded == demo_data.ROW_COUNTS
    assert max(e[2] for e in conn.log if e[0] == "executemany") <= seed_azure.BATCH_SIZE
    inserts = [e[1] for e in conn.log if e[0] == "executemany" and "dbo.orders " in e[1]]
    assert inserts[0] == "INSERT INTO dbo.orders (order_id, customer_id, order_date, total_amount, status, shipping_date) VALUES (?, ?, ?, ?, ?, ?)"


@pytest.mark.parametrize("existing", [{"customers": 1000}, {"orders": 0}, {"customers": 1000, "reviews": 3000, "orders": 5000}])
def test_it_refuses_to_touch_a_database_that_already_has_demo_tables(existing):
    code, out, conn, _ = run(["--yes"], Conn(existing=existing))
    assert code == 1 and "Refusing to seed" in out and "Nothing was changed" in out
    assert all(table in out for table in existing)
    writes = [e for e in conn.log if e[0] == "executemany" or (e[0] == "execute" and e[1].split()[0] in ("CREATE", "DROP"))]
    assert writes == [] and conn.committed == 0 and conn.closed == 1


def test_recreate_drops_children_first_then_rebuilds():
    code, out, conn, _ = run(["--yes", "--recreate"], Conn(existing={"customers": 1000}))
    assert code == 0 and conn.committed == 1
    ddl = [e[1] for e in conn.log if e[0] == "execute" and e[1].split()[0] in ("DROP", "CREATE")]
    drops = [s for s in ddl if s.startswith("DROP")]
    assert [s.split(".")[-1] for s in drops] == list(reversed(demo_data.TABLE_NAMES)) and ddl.index(drops[-1]) < ddl.index(next(s for s in ddl if s.startswith("CREATE")))


def test_a_failure_rolls_everything_back_and_never_leaks_the_connection_string_or_error_text():
    code, out, conn, _ = run(["--yes"], Conn(fail_on="CREATE TABLE dbo.orders"))
    assert code == 1 and conn.committed == 0 and conn.rolled_back == 1 and conn.closed == 1
    assert "rolled back" in out and "RuntimeError" in out and "top-secret" not in out and "boom" not in out


def test_the_connection_string_is_never_printed():
    for argv in (["--yes"], ["--yes", "--recreate"], []):
        _, out, _, _ = run(argv)
        assert "top-secret" not in out


def test_importing_the_script_does_not_require_pyodbc():
    import subprocess
    code = ("import sys; sys.modules['pyodbc'] = None; import importlib.util as u; "
            "s = u.spec_from_file_location('s', 'data/seed_azure.py'); m = u.module_from_spec(s); s.loader.exec_module(m); print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.stdout.strip() == "ok", out.stderr
