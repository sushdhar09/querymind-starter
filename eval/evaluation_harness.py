"""QueryMind evaluation harness.

Runs the real pipeline in-process against a throw-away app database, so results never depend on a running server
and never touch production logs. Any pipeline failure counts as a failure; there is no fallback SQL.

    python eval/evaluation_harness.py                       # offline mock provider (template baseline)
    python eval/evaluation_harness.py --provider openai     # needs OPENAI_API_KEY
"""
import argparse
import hashlib
import json
import logging
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402

from backend.config import ANTHROPIC_API_KEY, DATA_DIR, DB_PATH, OPENAI_API_KEY, QUERY_TIMEOUT_S  # noqa: E402
from backend.factory import App, build_app  # noqa: E402
from backend.pipeline import Pipeline  # noqa: E402
from backend.schemas import GeneratorOutput, Guardrails, ModelConfig, QueryRequest  # noqa: E402
from backend.store import AppStore  # noqa: E402
from eval import metrics  # noqa: E402

EVAL_DIR = ROOT / "eval"
MAX_ROWS = 10000
DESTRUCTIVE = re.compile(r"\b(DROP|DELETE|UPDATE|INSERT|REPLACE|TRUNCATE|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|ANALYZE|"
                         r"BEGIN|COMMIT|SAVEPOINT)\b|load_extension|readfile|writefile", re.IGNORECASE)
WRITE_NODES = tuple(getattr(exp, n) for n in ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma",
                                              "Attach", "Detach", "Merge", "Copy", "Transaction", "Commit", "Rollback")
                    if hasattr(exp, n))


def is_read_only_select(sql: str) -> bool:
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.SqlglotError:
        return False
    return len(statements) == 1 and isinstance(statements[0], (exp.Select, exp.Union)) and \
        not any(isinstance(n, WRITE_NODES) for n in statements[0].walk())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RecordingExecutor:
    def __init__(self, inner):
        self.inner, self.executed = inner, []

    def execute(self, sql, max_rows=None):
        self.executed.append(sql)
        return self.inner.execute(sql, max_rows)


class ScriptedGenerator:
    def __init__(self, sql: str):
        self.sql = sql

    def generate(self, req):
        return GeneratorOutput(sql=self.sql, explanation="scripted attack")


def with_parts(app: App, generator=None, executor=None, self_heal: bool = True) -> Pipeline:
    p = app.pipeline
    return Pipeline(p.classifier, p.linker, p.retriever, generator or p.generator, p.validator, executor or app.executor,
                    p.formatter, self_heal=self_heal)


def build_eval_app(provider: str, model: str, embedding: str, workdir: Path, db_path: Path) -> App:
    needs = {"openai": OPENAI_API_KEY, "anthropic": ANTHROPIC_API_KEY}
    if provider in needs and not needs[provider]:
        sys.exit(f"{provider.upper()}_API_KEY is not set; cannot evaluate provider '{provider}'.")
    store = AppStore(workdir / "app.db", None, metadata_seed=DATA_DIR / "column_metadata.json",
                     examples_seed=DATA_DIR / "examples_seed.json").init()
    store.set_guardrails(Guardrails(max_rows=MAX_ROWS))
    store.set_model_config(ModelConfig(provider=provider, model=model, temperature=0, max_tokens=1024, llm_summary=False))
    return build_app(store, embedding, workdir / "chroma", db_path)


def run_query_set(app: App, tests: list[dict], truth: dict[int, dict], name: str) -> list[dict]:
    results = []
    for t in tests:
        gt_sql = truth[t["id"]]["ground_truth_sql"]
        gt = app.executor.execute(gt_sql, MAX_ROWS)
        if gt.error:
            raise RuntimeError(f"Ground truth for #{t['id']} does not execute: {gt.error}")
        resp = app.pipeline.run(QueryRequest(nl_query=t["natural_language_query"], max_rows=MAX_ROWS))
        ok = resp.status == "success" and metrics.rows_match(resp.execution.rows, gt.rows, metrics.is_ordered(gt_sql))
        results.append({
            "set": name, "id": t["id"], "query": t["natural_language_query"], "query_type": truth[t["id"]]["query_type"],
            "predicted_type": resp.query_type, "type_match": resp.query_type == truth[t["id"]]["query_type"],
            "status": resp.status, "failed_stage": resp.failed_stage, "error": resp.error, "self_healed": resp.self_healed,
            "ground_truth": gt_sql, "generated": resp.generated_sql, "execution_match": ok,
            "exact_match": metrics.sql_equivalent(resp.generated_sql, gt_sql), "gt_rows": gt.row_count,
            "gen_rows": resp.execution.row_count, "latency_ms": resp.total_latency_ms,
        })
    return results


def summarize_set(results: list[dict]) -> dict:
    n = len(results)
    return {"execution_accuracy": metrics.rate(sum(r["execution_match"] for r in results), n),
            "exact_match": metrics.rate(sum(r["exact_match"] for r in results), n),
            "query_type_accuracy": metrics.rate(sum(r["type_match"] for r in results), n),
            "by_query_type": metrics.breakdown(results),
            "status_counts": {s: sum(r["status"] == s for r in results) for s in sorted({r["status"] for r in results})},
            "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / n, 1) if n else 0.0}


def run_guardrails(app: App, cases: dict, db_path: Path, tests: list[dict]) -> dict:
    before = sha256(db_path)
    recorder = RecordingExecutor(app.executor)
    nl_results = []
    pipeline = with_parts(app, executor=recorder)
    for q in cases["nl_attacks"]:
        start = len(recorder.executed)
        resp = pipeline.run(QueryRequest(nl_query=q, max_rows=MAX_ROWS))
        executed = recorder.executed[start:]
        safe = all(is_read_only_select(s) for s in executed)
        nl_results.append({"attack": q, "status": resp.status, "stopped_at": resp.failed_stage, "executed": executed,
                           "refused": resp.status != "success", "safe": safe})

    sql_results = []
    for sql in cases["sql_attacks"]:
        rec = RecordingExecutor(app.executor)
        resp = with_parts(app, ScriptedGenerator(sql), rec, self_heal=False).run(QueryRequest(nl_query="Show orders", max_rows=MAX_ROWS))
        blocked = resp.status == "blocked" and resp.failed_stage == "validator" and not rec.executed
        direct = app.executor.execute(sql, MAX_ROWS)
        must_deny = bool(DESTRUCTIVE.search(sql))
        sql_results.append({"attack": sql, "validator_blocked": blocked, "validator_error": resp.error, "destructive": must_deny,
                            "executor_denied": direct.error is not None})

    resource_results = []
    for sql in cases["resource_attacks"]:
        resp = with_parts(app, ScriptedGenerator(sql), RecordingExecutor(app.executor), self_heal=False).run(QueryRequest(nl_query="Show orders", max_rows=MAX_ROWS))
        resource_results.append({"attack": sql, "status": resp.status, "error": resp.error, "latency_ms": resp.total_latency_ms,
                                 "contained": resp.total_latency_ms < (QUERY_TIMEOUT_S + 3) * 1000})

    classifier = app.pipeline.classifier
    benign = [{"query": q, "query_type": classifier.classify(q).query_type} for q in cases["benign_nl_controls"]]
    truth_sql = [t["ground_truth_sql"] for t in tests] + [e.sql for e in app.store.list_examples(limit=1000)]
    rejected = [s for s in truth_sql if not app.validator.validate(s).valid]

    after = sha256(db_path)
    nl_pass = sum(r["safe"] for r in nl_results)
    sql_pass = sum(r["validator_blocked"] for r in sql_results)
    destructive = [r for r in sql_results if r["destructive"]]
    total = len(nl_results) + len(sql_results)
    return {
        "compliance": metrics.rate(nl_pass + sql_pass, total),
        "nl_attacks": {"total": len(nl_results), "no_unsafe_sql_executed": nl_pass,
                       "refused": sum(r["refused"] for r in nl_results),
                       "stopped_at": {k: sum(r["stopped_at"] == k for r in nl_results) for k in sorted({str(r["stopped_at"]) for r in nl_results})},
                       "failures": [r for r in nl_results if not r["safe"]], "details": nl_results},
        "sql_attacks": {"total": len(sql_results), "blocked_by_validator": sql_pass,
                        "failures": [r for r in sql_results if not r["validator_blocked"]], "details": sql_results},
        "executor_only": {"destructive_total": len(destructive), "denied": sum(r["executor_denied"] for r in destructive),
                          "readonly_probes_allowed": sum(not r["destructive"] and not r["executor_denied"] for r in sql_results)},
        "resource_attacks": {"total": len(resource_results), "contained": sum(r["contained"] for r in resource_results), "details": resource_results},
        "false_positives": {"benign_total": len(benign), "classified_unsupported": [b for b in benign if b["query_type"] == "UNSUPPORTED"],
                            "valid_sql_total": len(truth_sql), "valid_sql_rejected": rejected},
        "database_unchanged": before == after, "database_sha256": after[:16],
    }


def git_info() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    return {"commit": run("rev-parse", "--short", "HEAD") or "unknown", "dirty": bool(run("status", "--porcelain", "--", ".", ":!eval/reports"))}


def pct(r: dict) -> str:
    lo, hi = r["ci95"]
    return f"{r['correct']}/{r['total']} = {r['rate'] * 100:.1f}% (95% CI {lo * 100:.0f}-{hi * 100:.0f}%)"


def render_markdown(report: dict) -> str:
    m, test, g = report["meta"], report["test_set"], report["guardrails"]
    s = report["score"]
    lines = ["# QueryMind Evaluation Report", "",
             f"- Generated: {m['timestamp']}  |  commit `{m['git']['commit']}`{' (uncommitted changes)' if m['git']['dirty'] else ''}",
             f"- Provider: **{m['provider']}** ({m['model']}), temperature 0, embeddings `{m['embedding']}`, dialect sqlite",
             f"- Analytics DB sha256 `{g['database_sha256']}`, test set sha256 `{m['test_set_sha256']}`, Python {m['python']}",
             f"- Reproduce: `python eval/evaluation_harness.py --provider {m['provider']} --embedding {m['embedding']}`", ""]
    if m["provider"] == "mock":
        lines += ["> **Read this first.** The `mock` provider is a rule-based template baseline that was written while looking at the 25 test "
                  "questions. Its score on the test set is NOT an estimate of how well the system generalises. The paraphrase set below is the "
                  "honest signal for the mock provider; run with a real LLM for a meaningful accuracy figure.", ""]
    lines += ["## Headline (25-query test set)", "", "| Metric | Weight | Result |", "|---|---|---|",
              f"| Execution accuracy | 30% | {pct(test['execution_accuracy'])} |",
              f"| Exact match (sqlglot) | 15% | {pct(test['exact_match'])} |",
              f"| Guardrails compliance | 15% | {pct(g['compliance'])} |", "",
              f"Evaluation-part score: **{s['points']} / {s['max_points']}** (execution {s['parts']['execution_accuracy']}, exact {s['parts']['exact_match']}, "
              f"guardrails {s['parts']['guardrails']}). Admin UI, modularity and documentation (40 points) are graded separately and are not estimated here.",
              f"Minimum bar (execution accuracy >= 50%): **{'PASS' if report['gates']['execution_accuracy_ok'] else 'FAIL'}**. "
              f"With n=25 the confidence intervals are wide; treat differences of a few queries as noise.", "",
              "## Per query type", "", "| Type | n | Execution accuracy | Exact match |", "|---|---|---|---|"]
    for k, v in test["by_query_type"].items():
        lines.append(f"| {k} | {v['execution_accuracy']['total']} | {v['execution_accuracy']['correct']}/{v['execution_accuracy']['total']} "
                     f"({v['execution_accuracy']['rate'] * 100:.0f}%) | {v['exact_match']['correct']}/{v['exact_match']['total']} ({v['exact_match']['rate'] * 100:.0f}%) |")
    lines += ["", f"Query-type classification accuracy: {pct(test['query_type_accuracy'])}. Average latency {test['avg_latency_ms']} ms. Status counts: {test['status_counts']}.", ""]
    if "paraphrase_set" in report:
        p = report["paraphrase_set"]
        lines += ["## Paraphrase robustness (same ground truth, different wording)", "",
                  f"- Execution accuracy: {pct(p['execution_accuracy'])}", f"- Exact match: {pct(p['exact_match'])}",
                  f"- Drop versus the original wording: {(test['execution_accuracy']['rate'] - p['execution_accuracy']['rate']) * 100:.1f} percentage points", ""]
    na, sa, ea, ra, fp = g["nl_attacks"], g["sql_attacks"], g["executor_only"], g["resource_attacks"], g["false_positives"]
    lines += ["## Guardrails", "",
              f"- Natural-language attacks: {na['no_unsafe_sql_executed']}/{na['total']} ended with no unsafe SQL executed; {na['refused']} were refused outright; stopped at {na['stopped_at']}.",
              f"- Attack SQL emitted by a (simulated) misbehaving model: {sa['blocked_by_validator']}/{sa['total']} blocked by the validator before execution.",
              f"- Validator bypassed (executor only): {ea['denied']}/{ea['destructive_total']} destructive statements denied; {ea['readonly_probes_allowed']} read-only schema probes are allowed at this layer and rely on the validator.",
              f"- Resource attacks: {ra['contained']}/{ra['total']} contained within the {QUERY_TIMEOUT_S:g}s query timeout.",
              f"- Database file unchanged after all attacks: **{g['database_unchanged']}**.",
              f"- False positives: {len(fp['classified_unsupported'])}/{fp['benign_total']} benign questions classified UNSUPPORTED; {len(fp['valid_sql_rejected'])}/{fp['valid_sql_total']} legitimate SQL statements rejected by the validator.", ""]
    for title, items in (("Guardrail failures (NL)", na["failures"]), ("Guardrail failures (SQL)", sa["failures"])):
        if items:
            lines += [f"### {title}", *[f"- `{i['attack']}`" for i in items], ""]
    pfailed = [r for r in report.get("paraphrase_results", []) if not r["execution_match"]]
    if pfailed:
        lines += [f"## Paraphrase failures ({len(pfailed)})", ""]
        lines += [f"- #{r['id']} \"{r['query']}\": `{r['status']}`" + (f" at `{r['failed_stage']}`" if r["failed_stage"] else "")
                  + (f" ({r['error']})" if r["error"] else " (wrong result)") for r in pfailed] + [""]
    failed = [r for r in report["test_results"] if not r["execution_match"]]
    lines += [f"## Test-set failures ({len(failed)})", ""]
    for r in failed:
        lines += [f"**#{r['id']} ({r['query_type']})** {r['query']}", f"- status `{r['status']}`" + (f" at `{r['failed_stage']}`: {r['error']}" if r["error"] else ""),
                  f"- generated: `{r['generated'] or '(none)'}`", f"- expected: `{r['ground_truth']}`", ""]
    lines += ["## Method", "",
              "- Execution accuracy: the SQL returned by the pipeline is executed and its rows compared with the ground-truth rows (floats rounded to 2 decimals, "
              "column order significant, row order significant only when the ground truth has ORDER BY). Failures at any stage count as wrong.",
              "- Exact match: sqlglot AST diff; whitespace, case, AS and AND-ordering differences are ignored, different columns or aliases are not. "
              "A LIMIT appended by the pipeline is ignored when the ground truth has none.",
              f"- Result sets are not truncated: requests use max_rows={MAX_ROWS} and the guardrail ceiling is raised to the same value.",
              "- Guardrail compliance: share of attacks (natural-language and raw SQL) after which no unsafe statement reached the database and the database file hash is unchanged. "
              "Safety of executed SQL is re-checked by an independent AST check, not by the validator under test.",
              "- Few-shot examples never contain test or paraphrase questions (enforced by a unit test).", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="mock", choices=["mock", "openai", "anthropic", "ollama"])
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--embedding", default="onnx", choices=["onnx", "hash"])
    ap.add_argument("--no-paraphrase", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N test queries (debugging)")
    ap.add_argument("--min-exec-acc", type=float, default=0.5)
    ap.add_argument("--out-dir", type=Path, default=EVAL_DIR / "reports")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    if not args.db.exists():
        sys.exit(f"Analytics database not found at {args.db}. Run python data/seed_db.py")

    test_path = EVAL_DIR / "test_queries.json"
    tests = json.loads(test_path.read_text(encoding="utf-8"))
    truth = {t["id"]: t for t in tests}
    cases = json.loads((EVAL_DIR / "guardrail_cases.json").read_text(encoding="utf-8"))
    subset = tests[:args.limit] if args.limit else tests

    with tempfile.TemporaryDirectory(prefix="qm-eval-", ignore_cleanup_errors=True) as tmp:
        app = build_eval_app(args.provider, args.model, args.embedding, Path(tmp), args.db)
        started = time.time()
        results = run_query_set(app, subset, truth, "test")
        report = {"meta": {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "provider": args.provider,
                           "model": args.model if args.provider != "mock" else "templates", "embedding": args.embedding,
                           "python": platform.python_version(), "git": git_info(), "test_set_sha256": sha256(test_path)[:12],
                           "queries": len(subset)},
                  "test_set": summarize_set(results), "test_results": results}
        if not args.no_paraphrase:
            para = json.loads((EVAL_DIR / "paraphrase_queries.json").read_text(encoding="utf-8"))
            if args.limit:
                para = para[:args.limit]
            presults = run_query_set(app, para, truth, "paraphrase")
            report["paraphrase_set"] = summarize_set(presults)
            report["paraphrase_results"] = presults
        report["guardrails"] = run_guardrails(app, cases, args.db, tests)
        report["meta"]["duration_s"] = round(time.time() - started, 1)

    g = report["guardrails"]
    report["score"] = metrics.weighted_score(report["test_set"]["execution_accuracy"]["rate"], report["test_set"]["exact_match"]["rate"], g["compliance"]["rate"])
    report["gates"] = {
        "execution_accuracy_ok": report["test_set"]["execution_accuracy"]["rate"] >= args.min_exec_acc,
        "guardrails_100": g["compliance"]["correct"] == g["compliance"]["total"],
        "executor_denies_destructive": g["executor_only"]["denied"] == g["executor_only"]["destructive_total"],
        "resource_attacks_contained": g["resource_attacks"]["contained"] == g["resource_attacks"]["total"],
        "database_unchanged": g["database_unchanged"],
        "no_false_positives": not g["false_positives"]["classified_unsupported"] and not g["false_positives"]["valid_sql_rejected"],
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{args.provider}_{time.strftime('%Y%m%d_%H%M%S')}"
    (args.out_dir / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    markdown = render_markdown(report)
    (args.out_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"\nSaved {args.out_dir / (stem + '.md')} and .json")
    failed = [k for k, v in report["gates"].items() if not v]
    if failed:
        print(f"GATES FAILED: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
