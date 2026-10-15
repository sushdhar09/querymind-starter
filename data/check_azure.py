"""Read-only health check of your Azure SQL database against what QueryMind expects. Run it yourself:

    python data/check_azure.py [--timeout 15] [--strict] [--json report.json]

It never writes. It uses AZURE_SQL_CONNECTION_STRING (the app's login) and checks:
  1. connectivity and that the login has no write permission,
  2. the schema (tables, columns, types, foreign keys) against the demo schema,
  3. row counts and category values against the demo dataset,
  4. that every shipped query (curated examples, held-out ground truth, mock templates) passes the validator and RUNS on Azure,
  5. that data-independent results equal what the in-memory test double returns for the same SQL.

Exit code 1 if anything FAILs (connection, writable login, missing tables or columns, a query that errors). Differences are
WARNs (your data may simply differ from the demo data) unless --strict is given.
"""
import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402

from backend import demo_data, tsql_rules  # noqa: E402
from backend.components.sql_executor import SqlExecutor  # noqa: E402
from backend.components.sql_generator import SqlGenerator  # noqa: E402
from backend.components.sql_validator import SqlValidator  # noqa: E402
from backend.db import AnalyticsDatabase, DatabaseError  # noqa: E402
from eval import metrics  # noqa: E402

CATEGORY_COLUMNS = [("orders", "status"), ("payments", "status"), ("payments", "payment_method"), ("products", "category"),
                    ("customers", "city")]
DATE_COLUMNS = {c.name.lower() for t in demo_data.SCHEMA for c in t.columns if demo_data.base_type(c.sql_type) == "DATE"}
DATE_FUNCTIONS = {"getdate", "dateadd", "datediff", "year", "month", "day", "format", "current_timestamp", "sysdatetime", "datepart"}
MAX_ROWS = 10000


@dataclass
class Result:
    status: str
    check: str
    detail: str = ""


def _clause_columns(tree: exp.Expression, clauses: tuple[str, ...]) -> set[str]:
    names = set()
    for select in tree.find_all(exp.Select):
        for clause in clauses:
            node = select.args.get(clause)
            if node is not None:
                names |= {c.name.lower() for c in node.find_all(exp.Column)}
    return names


def comparison_level(sql: str) -> str:
    """'none': depends on today's date, only check that it runs. 'shape': compare columns and row count.
    'full': compare every value."""
    tokens = tsql_rules.tokenize(sql)
    if any(t.text.lower() in DATE_FUNCTIONS for t in tokens):
        return "none"
    tree = sqlglot.parse_one(sql, read=tsql_rules.DIALECT)
    if _clause_columns(tree, ("where", "group", "having")) & DATE_COLUMNS:
        return "none"
    selected = {c.name.lower() for s in tree.find_all(exp.Select) for e in s.expressions for c in e.find_all(exp.Column)}
    if any(isinstance(n, exp.Star) and not isinstance(n.parent, exp.Count) for n in tree.walk()) or selected & DATE_COLUMNS:
        return "shape"
    return "full"


def shipped_queries() -> list[tuple[str, str]]:
    seen, queries = set(), []
    sources = [("example", e["question"], e["sql"]) for e in json.loads((ROOT / "data" / "examples_seed.json").read_text(encoding="utf-8"))]
    sources += [("ground truth", f"#{t['id']} {t['natural_language_query']}", t["ground_truth_sql"])
                for t in json.loads((ROOT / "eval" / "test_queries.json").read_text(encoding="utf-8"))]
    sources += [("template", key, sql) for key, sql in SqlGenerator().templates.items()]
    for kind, label, sql in sources:
        if sql not in seen:
            seen.add(sql)
            queries.append((f"{kind}: {label}", sql))
    return queries


def check_connection(database: AnalyticsDatabase, results: list[Result]) -> bool:
    try:
        database.ping()
    except DatabaseError as e:
        results.append(Result("FAIL", "connection", str(e)))
        return False
    results.append(Result("OK", "connection"))
    try:
        status = database.read_only_status()
    except DatabaseError as e:
        results.append(Result("FAIL", "read-only login", f"could not inspect permissions: {e}"))
        return True
    results.append(Result("OK" if status.read_only else "FAIL", "read-only login", status.detail))
    return True


def check_schema(database: AnalyticsDatabase, results: list[Result]):
    schema = database.load_schema()
    found = {t.lower(): {c.name.lower(): c.type for c in cols} for t, cols in schema.tables.items()}
    ok = True
    for table in demo_data.SCHEMA:
        columns = found.get(table.name)
        if columns is None:
            results.append(Result("FAIL", f"table {table.name}", "missing"))
            ok = False
            continue
        for column in table.columns:
            if column.name not in columns:
                results.append(Result("FAIL", f"column {table.name}.{column.name}", "missing"))
                ok = False
            elif columns[column.name].upper() != demo_data.base_type(column.sql_type):
                results.append(Result("WARN", f"column {table.name}.{column.name}",
                                      f"type is {columns[column.name]}, expected {demo_data.base_type(column.sql_type)}"))
        extra = sorted(set(columns) - {c.name for c in table.columns})
        if extra:
            results.append(Result("WARN", f"table {table.name}", f"extra columns the examples do not know about: {', '.join(extra)}"))
    fks = {(t.lower(), c.lower(), rt.lower(), rc.lower()) for t, c, rt, rc in schema.foreign_keys}
    expected = {(t.name, c.name, *c.references.split(".")) for t in demo_data.SCHEMA for c in t.columns if c.references}
    if expected - fks:
        results.append(Result("WARN", "foreign keys", "missing, so join paths cannot be inferred for: "
                                                       + ", ".join(f"{a}.{b}->{c}.{d}" for a, b, c, d in sorted(expected - fks))))
    elif ok:
        results.append(Result("OK", "schema", f"{len(demo_data.SCHEMA)} tables, all expected columns and {len(expected)} foreign keys"))
    return ok, schema


def check_data(database: AnalyticsDatabase, reference: AnalyticsDatabase, results: list[Result]) -> None:
    for table in demo_data.TABLE_NAMES:
        try:
            count = database.query(f"SELECT COUNT(*) AS n FROM {table}", 1, 30).rows[0][0]
        except DatabaseError as e:
            results.append(Result("FAIL", f"row count {table}", str(e)))
            continue
        expected = reference.query(f"SELECT COUNT(*) AS n FROM {table}", 1, 30).rows[0][0]
        results.append(Result("OK" if count == expected else "WARN", f"row count {table}",
                              f"{count} rows" if count == expected else f"{count} rows, the demo dataset has {expected}"))
    for table, column in CATEGORY_COLUMNS:
        try:
            actual = {str(v) for v in database.sample_values(table, column, 100)}
        except DatabaseError as e:
            results.append(Result("FAIL", f"values {table}.{column}", str(e)))
            continue
        wanted = {str(v) for v in reference.sample_values(table, column, 100)}
        results.append(Result("OK" if actual == wanted else "WARN", f"values {table}.{column}",
                              "same categories" if actual == wanted else f"differs: only in Azure {sorted(actual - wanted)[:5]}, only in demo {sorted(wanted - actual)[:5]}"))


def check_queries(database: AnalyticsDatabase, reference: AnalyticsDatabase, schema, timeout_s: float, results: list[Result]) -> None:
    validator = SqlValidator(schema={t: {c.name.lower() for c in cols} for t, cols in schema.tables.items()})
    azure, local = SqlExecutor(database, timeout_s), SqlExecutor(reference, timeout_s)
    for label, sql in shipped_queries():
        checked = validator.validate(sql, max_rows=MAX_ROWS)
        if not checked.valid:
            results.append(Result("FAIL", label, f"validator rejects it: {checked.errors}"))
            continue
        remote = azure.execute(checked.sanitized_sql, MAX_ROWS)
        if remote.error:
            results.append(Result("FAIL", label, f"does not run on Azure: {remote.error}"))
            continue
        level = comparison_level(checked.sanitized_sql)
        if level == "none":
            results.append(Result("OK" if remote.row_count else "WARN", label, f"runs ({remote.row_count} rows; depends on today's date, not compared)"))
            continue
        expected = local.execute(checked.sanitized_sql, MAX_ROWS)
        if expected.error:
            results.append(Result("WARN", label, f"runs on Azure ({remote.row_count} rows); the test double could not run it: {expected.error}"))
        elif [c.lower() for c in remote.columns] != [c.lower() for c in expected.columns] and all(remote.columns):
            results.append(Result("WARN", label, f"column names differ: Azure {remote.columns} vs double {expected.columns}"))
        elif remote.row_count != expected.row_count:
            results.append(Result("WARN", label, f"{remote.row_count} rows on Azure, {expected.row_count} on the test double"))
        elif level == "full" and not metrics.rows_match(remote.rows, expected.rows, metrics.is_ordered(checked.sanitized_sql)):
            results.append(Result("WARN", label, "same row count but different values (the double approximates SQL Server, or your data differs)"))
        else:
            results.append(Result("OK", label, f"{remote.row_count} rows, {'values' if level == 'full' else 'shape'} match the test double"))


def run_checks(database: AnalyticsDatabase, reference: AnalyticsDatabase, timeout_s: float = 15) -> list[Result]:
    results: list[Result] = []
    if not check_connection(database, results):
        return results
    try:
        schema_ok, schema = check_schema(database, results)
    except DatabaseError as e:
        results.append(Result("FAIL", "schema", f"could not read the schema: {e}"))
        return results
    if not schema_ok:
        results.append(Result("FAIL", "queries", "skipped because required tables or columns are missing"))
        return results
    check_data(database, reference, results)
    check_queries(database, reference, schema, timeout_s, results)
    return results


def render(results: list[Result], out: Callable[[str], None] = print) -> dict:
    for r in results:
        out(f"[{r.status}] {r.check}" + (f": {r.detail}" if r.detail else ""))
    counts = {s: sum(r.status == s for r in results) for s in ("OK", "WARN", "FAIL")}
    out(f"\n{counts['OK']} ok, {counts['WARN']} warnings, {counts['FAIL']} failures")
    return counts


def main(argv: Optional[list[str]] = None, database: Optional[AnalyticsDatabase] = None, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=float, default=15, help="seconds allowed per query")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ap.add_argument("--json", type=Path, default=None, help="also write the results as JSON")
    args = ap.parse_args(argv)
    if database is None:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        import os

        from backend.db import AzureSqlDatabase
        connection_string = os.getenv("AZURE_SQL_CONNECTION_STRING", "")
        if not connection_string:
            out("AZURE_SQL_CONNECTION_STRING is not set. Put the app's read-only connection string in .env.")
            return 1
        database = AzureSqlDatabase(connection_string)
    from backend.testing.inmemory_db import InMemoryDatabase
    results = run_checks(database, InMemoryDatabase(), args.timeout)
    counts = render(results, out)
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    return 1 if counts["FAIL"] or (args.strict and counts["WARN"]) else 0


if __name__ == "__main__":
    sys.exit(main())
