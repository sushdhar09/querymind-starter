import json
from pathlib import Path

import pytest

import backend.components.sql_executor as sql_executor
import eval.evaluation_harness as harness
from backend.db import DatabaseError, ReadOnlyStatus
from backend.schemas import GeneratorOutput, ValidatorOutput
from backend.testing.inmemory_db import InMemoryDatabase
from eval import metrics

ROOT = Path(__file__).resolve().parent.parent


def test_rows_match_is_order_insensitive_unless_ordered():
    a, b = [[1, "x"], [2, "y"]], [[2, "y"], [1, "x"]]
    assert metrics.rows_match(a, b) and not metrics.rows_match(a, b, ordered=True)
    assert metrics.rows_match(a, a, ordered=True)


def test_rows_match_rejects_wrong_content_shape_and_count():
    assert not metrics.rows_match([[1, "x"]], [[1, "z"]])
    assert not metrics.rows_match([[1]], [[1, "x"]])
    assert not metrics.rows_match([[1], [1]], [[1]])
    assert not metrics.rows_match([[1, 2]], [[2, 1]], ordered=True)
    assert not metrics.rows_match([[None]], [["None"]])


def test_rows_match_rounds_floats_and_keeps_duplicates():
    assert metrics.rows_match([[1.004]], [[1.0]])
    assert not metrics.rows_match([[1.2]], [[1.3]])
    assert not metrics.rows_match([[1], [1], [2]], [[1], [2], [2]])


def test_is_ordered():
    assert metrics.is_ordered("SELECT a FROM t ORDER BY a")
    assert not metrics.is_ordered("SELECT a FROM t")
    assert not metrics.is_ordered("not sql at all ((")


@pytest.mark.parametrize("gen,truth,expected", [
    ("select status, count(*) from orders group by status", "SELECT status, COUNT(*) FROM orders GROUP BY status", True),
    ("SELECT p.name FROM products AS p", "SELECT p.name FROM products p", True),
    ("SELECT a FROM t WHERE x = 1 AND y = 2", "SELECT a FROM t WHERE y = 2 AND x = 1", True),
    ("SELECT TOP 100 * FROM orders", "SELECT * FROM orders", True),
    ("SELECT TOP 5 * FROM orders", "SELECT TOP 10 * FROM orders", False),
    ("SELECT AVG(CAST(x AS FLOAT)) AS a FROM t", "SELECT AVG(x) AS a FROM t", False),
    ("SELECT a FROM t", "SELECT b FROM t", False),
    ("SELECT SUM(x) AS s FROM t", "SELECT SUM(x) AS total FROM t", False),
    ("SELECT a FROM t WHERE x = 1", "SELECT a FROM t WHERE x = 2", False),
    ("SELECT a FROM t", "SELECT a FROM u", False),
    ("", "SELECT 1", False),
    ("SELEC nonsense ((", "SELECT 1", False),
])
def test_sql_equivalent(gen, truth, expected):
    assert metrics.sql_equivalent(gen, truth) is expected


def test_wilson_interval():
    assert metrics.wilson(0, 0) == (0.0, 0.0)
    lo, hi = metrics.wilson(25, 25)
    assert 0.85 < lo < 0.9 and hi == 1.0
    lo, hi = metrics.wilson(5, 10)
    assert lo < 0.5 < hi and 0.2 < lo and hi < 0.8
    assert metrics.rate(3, 4)["rate"] == 0.75 and metrics.rate(0, 0)["rate"] == 0.0


def test_breakdown_and_weighted_score():
    rows = [{"query_type": "A", "execution_match": True, "exact_match": False},
            {"query_type": "A", "execution_match": False, "exact_match": False},
            {"query_type": "B", "execution_match": True, "exact_match": True}]
    b = metrics.breakdown(rows)
    assert b["A"]["execution_accuracy"]["correct"] == 1 and b["A"]["execution_accuracy"]["total"] == 2
    assert b["B"]["exact_match"]["rate"] == 1.0
    assert metrics.weighted_score(1, 1, 1)["points"] == 60
    assert metrics.weighted_score(0.5, 0, 1) == {"points": 30.0, "max_points": 60, "parts": {"execution_accuracy": 15.0, "exact_match": 0.0, "guardrails": 15.0}}


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1", True), ("WITH a AS (SELECT 1) SELECT * FROM a", True), ("SELECT 1 UNION SELECT 2", True),
    ("DELETE FROM orders", False), ("SELECT 1; DROP TABLE x", False), ("PRAGMA foreign_keys=OFF", False),
    ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", False), ("not sql ((", False),
    ("SELECT TOP 5 * FROM orders", True), ("SELECT * INTO copy FROM orders", False), ("EXEC sp_who", False),
    ("WITH d AS (SELECT * FROM orders) DELETE FROM d", False), ("SELECT 1; EXEC sp_who", False),
])
def test_independent_read_only_check(sql, expected):
    assert harness.is_read_only_select(sql) is expected


def test_fixture_files_are_consistent():
    tests = json.loads((ROOT / "eval" / "test_queries.json").read_text())
    para = json.loads((ROOT / "eval" / "paraphrase_queries.json").read_text())
    cases = json.loads((ROOT / "eval" / "guardrail_cases.json").read_text())
    ids = {t["id"] for t in tests}
    assert len(tests) == 25 and {p["id"] for p in para} == ids and len(para) == 25
    assert not {p["natural_language_query"].lower() for p in para} & {t["natural_language_query"].lower() for t in tests}
    assert len(cases["nl_attacks"]) >= 40 and len(cases["sql_attacks"]) >= 45 and cases["benign_nl_controls"] and cases["resource_attacks"]


def test_examples_do_not_contain_paraphrases():
    examples = {e["question"].lower() for e in json.loads((ROOT / "data" / "examples_seed.json").read_text())}
    para = {p["natural_language_query"].lower() for p in json.loads((ROOT / "eval" / "paraphrase_queries.json").read_text())}
    assert not examples & para


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(sql_executor, "QUERY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(harness, "QUERY_TIMEOUT_S", 0.5)
    return harness.build_eval_app("mock", "m", "hash", tmp_path, InMemoryDatabase())


@pytest.fixture
def tests_and_truth():
    tests = json.loads((ROOT / "eval" / "test_queries.json").read_text())
    return tests, {t["id"]: t for t in tests}


def test_harness_scores_correct_sql_as_correct(app, tests_and_truth):
    tests, truth = tests_and_truth
    results = harness.run_query_set(app, tests[:4], truth, "test")
    assert all(r["execution_match"] and r["exact_match"] and r["status"] == "success" for r in results)


def test_harness_scores_wrong_results_as_wrong(app, tests_and_truth):
    tests, truth = tests_and_truth

    class Wrong:
        def generate(self, req):
            return GeneratorOutput(sql="SELECT status, COUNT(*) AS n FROM orders GROUP BY status", explanation="")

    app.pipeline = harness.with_parts(app, generator=Wrong())
    results = harness.run_query_set(app, [tests[0]], truth, "test")
    assert results[0]["status"] == "success" and not results[0]["execution_match"] and not results[0]["exact_match"]


def test_harness_never_substitutes_fallback_sql(app, tests_and_truth):
    tests, truth = tests_and_truth

    class Broken:
        def generate(self, req):
            raise RuntimeError("model down")

    app.pipeline = harness.with_parts(app, generator=Broken())
    results = harness.run_query_set(app, tests[:3], truth, "test")
    assert all(not r["execution_match"] and r["status"] == "error" and r["generated"] == "" for r in results)
    assert harness.summarize_set(results)["execution_accuracy"]["rate"] == 0


def test_harness_detects_truncated_results(app, tests_and_truth):
    tests, truth = tests_and_truth
    app.store.set_guardrails(app.store.get_guardrails().model_copy(update={"max_rows": 100}))
    results = harness.run_query_set(app, [t for t in tests if t["id"] == 9], truth, "test")
    assert not results[0]["execution_match"] and results[0]["gt_rows"] == 5000


def test_guardrail_suite_passes_for_the_real_validator(app, tests_and_truth):
    tests, _ = tests_and_truth
    cases = json.loads((ROOT / "eval" / "guardrail_cases.json").read_text())
    report = harness.run_guardrails(app, cases, tests)
    assert report["compliance"]["correct"] == report["compliance"]["total"] == len(cases["nl_attacks"]) + len(cases["sql_attacks"])
    assert report["executor_only"]["denied"] == report["executor_only"]["destructive_total"] > 30
    assert report["resource_attacks"]["contained"] == len(cases["resource_attacks"])
    assert report["database_unchanged"] and not report["false_positives"]["classified_unsupported"] and not report["false_positives"]["valid_sql_rejected"]


def test_guardrail_suite_catches_a_permissive_validator(app, tests_and_truth):
    tests, _ = tests_and_truth

    class AllowEverything:
        def validate(self, sql, max_rows=100, dialect="tsql"):
            return ValidatorOutput(valid=True, sanitized_sql=sql)

    app.pipeline.validator = AllowEverything()
    cases = json.loads((ROOT / "eval" / "guardrail_cases.json").read_text())
    report = harness.run_guardrails(app, cases, tests)
    assert report["sql_attacks"]["blocked_by_validator"] < len(cases["sql_attacks"]) // 2
    assert report["compliance"]["correct"] < report["compliance"]["total"]
    assert report["database_unchanged"], "the executor layer must still protect the data"


def test_cli_writes_reports_and_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(sql_executor, "QUERY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(harness, "QUERY_TIMEOUT_S", 0.5)
    code = harness.main(["--embedding", "hash", "--limit", "3", "--out-dir", str(tmp_path)])
    files = {p.suffix: p for p in tmp_path.iterdir()}
    assert code == 0 and set(files) == {".json", ".md"}
    report = json.loads(files[".json"].read_text())
    assert report["meta"]["queries"] == 3 and "paraphrase_set" in report and all(report["gates"].values())
    md = files[".md"].read_text()
    assert "## Per query type" in md and "Read this first" in md and "95% CI" in md and "in-memory test double, not SQL Server" in md
    assert report["meta"]["database_kind"] == "memory" and report["guardrails"]["resource_attacks"]["skipped"] is False


def test_cli_fails_when_a_gate_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(sql_executor, "QUERY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(harness, "QUERY_TIMEOUT_S", 0.5)
    assert harness.main(["--embedding", "hash", "--limit", "3", "--no-paraphrase", "--min-exec-acc", "1.01", "--out-dir", str(tmp_path)]) == 1


def test_database_unchanged_is_detected_by_the_fingerprint(app, tests_and_truth):
    tests, _ = tests_and_truth
    cases = {"nl_attacks": [], "sql_attacks": [], "resource_attacks": [], "benign_nl_controls": []}
    assert harness.run_guardrails(app, cases, tests)["database_unchanged"]
    calls = []
    original = app.database.fingerprint

    def drifting():
        calls.append(1)
        data = original()
        return {**data, "orders": data["orders"] + ("x" * (len(calls) - 1))}

    app.database.fingerprint = drifting
    assert not harness.run_guardrails(app, cases, tests)["database_unchanged"]


def test_resource_attacks_can_be_skipped(app, tests_and_truth):
    tests, _ = tests_and_truth
    cases = json.loads((ROOT / "eval" / "guardrail_cases.json").read_text())
    report = harness.run_guardrails(app, cases, tests, resource_attacks=False)
    assert report["resource_attacks"] == {"skipped": True, "total": 0, "contained": 0, "details": []}


class FakeAzure(InMemoryDatabase):
    def __init__(self, read_only=True, ping_error=None):
        super().__init__()
        self._ro, self._ping_error = read_only, ping_error

    def read_only_status(self):
        return ReadOnlyStatus(self._ro, "login can write: db_owner" if not self._ro else "ok")

    def ping(self):
        if self._ping_error:
            raise DatabaseError(self._ping_error)


def test_azure_mode_refuses_a_login_that_can_write(monkeypatch):
    monkeypatch.setattr(harness, "build_database", lambda *a, **k: FakeAzure(read_only=False))
    with pytest.raises(SystemExit, match="Refusing to evaluate against a login that can write.*db_owner"):
        harness.open_database("azure")


def test_azure_mode_reports_an_unreachable_server(monkeypatch):
    monkeypatch.setattr(harness, "build_database", lambda *a, **k: FakeAzure(ping_error="Could not connect to Azure SQL (08001)"))
    with pytest.raises(SystemExit, match="Cannot use Azure SQL: Could not connect to Azure SQL .08001."):
        harness.open_database("azure")


def test_azure_mode_skips_resource_attacks_unless_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "build_database", lambda *a, **k: FakeAzure())
    monkeypatch.setattr(sql_executor, "QUERY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(harness, "QUERY_TIMEOUT_S", 0.5)
    args = ["--embedding", "hash", "--limit", "2", "--no-paraphrase", "--database", "azure"]
    assert harness.main(args + ["--out-dir", str(tmp_path / "a")]) == 0
    skipped = json.loads(next((tmp_path / "a").glob("*.json")).read_text())
    assert skipped["meta"]["database_kind"] == "azure" and skipped["guardrails"]["resource_attacks"]["skipped"] is True
    md = next((tmp_path / "a").glob("*.md")).read_text()
    assert "Resource attacks: skipped" in md and "Database: **Azure SQL**" in md and "in-memory test double, not SQL Server" not in md
    assert harness.main(args + ["--azure-resource-attacks", "--out-dir", str(tmp_path / "b")]) == 0
    ran = json.loads(next((tmp_path / "b").glob("*.json")).read_text())
    assert ran["guardrails"]["resource_attacks"]["skipped"] is False and ran["guardrails"]["resource_attacks"]["total"] == 2
