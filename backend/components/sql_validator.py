import logging
import sqlite3
from typing import Callable, Mapping, Union

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.config import DB_PATH
from backend.schemas import Guardrails, ValidatorOutput

log = logging.getLogger(__name__)

SchemaMap = Mapping[str, set[str]]

TOGGLEABLE_OPERATIONS: dict[str, tuple[type, ...]] = {
    "DROP": (exp.Drop,),
    "DELETE": (exp.Delete,),
    "UPDATE": (exp.Update,),
    "INSERT": (exp.Insert,),
    "TRUNCATE": (exp.TruncateTable,),
    "ALTER": (exp.Alter,),
    "CREATE": (exp.Create,),
}
ALWAYS_BLOCKED = tuple(
    getattr(exp, n) for n in (
        "Pragma", "Attach", "Detach", "Command", "Install", "LoadData", "Copy", "Set", "Transaction", "Commit",
        "Rollback", "Use", "Merge", "Analyze",
    ) if hasattr(exp, n)
)
BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zeroblob", "randomblob"}
QUERY_ROOTS = (exp.Select, exp.Union)


def load_sqlite_schema(db_path=DB_PATH) -> dict[str, set[str]]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")]
        return {t: {c[1].lower() for c in conn.execute(f'PRAGMA table_info("{t}")')} for t in tables}
    finally:
        conn.close()


class SqlValidator:
    def __init__(self, schema: Union[SchemaMap, Callable[[], SchemaMap], None] = None,
                 guardrails: Union[Guardrails, Callable[[], Guardrails], None] = None):
        self._schema = schema or load_sqlite_schema
        self._guardrails = guardrails or Guardrails()

    def _get_schema(self) -> dict[str, set[str]]:
        s = self._schema() if callable(self._schema) else self._schema
        return {t.lower(): {c.lower() for c in cols} for t, cols in s.items()}

    def _get_guardrails(self) -> Guardrails:
        return self._guardrails() if callable(self._guardrails) else self._guardrails

    def validate(self, sql: str, max_rows: int = 100, dialect: str = "sqlite") -> ValidatorOutput:
        errors = self._check(sql, max_rows, dialect)
        if isinstance(errors, list):
            return ValidatorOutput(valid=False, sanitized_sql=sql, errors=errors)
        return ValidatorOutput(valid=True, sanitized_sql=errors.sql(dialect=dialect, comments=False))

    def _check(self, sql: str, max_rows: int, dialect: str) -> Union[list[str], exp.Expression]:
        g = self._get_guardrails()
        try:
            statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
        except SqlglotError as e:
            return [f"Syntax error: {str(e).splitlines()[0]}"]
        if len(statements) != 1:
            return ["Exactly one SQL statement is allowed"]
        tree = statements[0]

        blocked_ops = {o.upper() for o in g.blocked_operations}
        toggled_off = tuple(c for op, classes in TOGGLEABLE_OPERATIONS.items() if op not in blocked_ops for c in classes)
        is_query = isinstance(tree, QUERY_ROOTS)
        if not is_query and not (toggled_off and isinstance(tree, toggled_off)):
            op = next((o for o, cs in TOGGLEABLE_OPERATIONS.items() if isinstance(tree, cs)), None)
            return [f"{op} statements are blocked by guardrails" if op else "Only SELECT statements are allowed"]

        errors: list[str] = []
        for node in tree.walk():
            if isinstance(node, ALWAYS_BLOCKED):
                errors.append(f"{type(node).__name__.upper()} is not allowed")
            for op, classes in TOGGLEABLE_OPERATIONS.items():
                if op in blocked_ops and isinstance(node, classes):
                    errors.append(f"{op} statements are blocked by guardrails")
            if isinstance(node, exp.Anonymous) and (node.name.lower() in BLOCKED_FUNCTIONS or node.name.lower().startswith("pragma_")):
                errors.append(f"Function {node.name} is not allowed")
            if isinstance(node, exp.Func) and not isinstance(node, exp.Anonymous) and node.sql_name().lower() in BLOCKED_FUNCTIONS:
                errors.append(f"Function {node.sql_name()} is not allowed")
        if errors:
            return sorted(set(errors))

        errors = self._check_schema(tree, g, check_columns=is_query)
        if errors:
            return errors
        if is_query:
            tree = self._apply_limit(tree, min(max_rows, g.max_rows))
        return tree

    def _check_schema(self, tree: exp.Expression, g: Guardrails, check_columns: bool) -> list[str]:
        schema = self._get_schema()
        errors: list[str] = []
        derived = {c.alias.lower() for c in tree.find_all(exp.CTE)} | {
            s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
        blocked_tables = {t.lower() for t in g.blocked_tables}
        prefixes = tuple(p.lower() for p in g.allowed_table_prefixes)

        alias_to_table: dict[str, str] = {d: "<derived>" for d in derived}
        real_tables: set[str] = set()
        for t in tree.find_all(exp.Table):
            if not isinstance(t.this, exp.Identifier):
                errors.append("Table-valued functions are not allowed")
                continue
            name = t.name.lower()
            if name in derived and not t.args.get("db"):
                alias_to_table[(t.alias or name).lower()] = "<derived>"
                continue
            if t.args.get("db") or t.args.get("catalog"):
                errors.append(f"Schema-qualified table '{t.sql()}' is not allowed")
            elif name in blocked_tables or name.startswith("sqlite_"):
                errors.append(f"Table '{t.name}' is not accessible")
            elif name not in schema:
                errors.append(f"Unknown table '{t.name}'")
            elif prefixes and not name.startswith(prefixes):
                errors.append(f"Table '{t.name}' is outside the allowed scope")
            else:
                real_tables.add(name)
                alias_to_table[(t.alias or name).lower()] = name
        if errors or not check_columns:
            return sorted(set(errors))

        defined_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}
        known_columns = set().union(*(schema[t] for t in real_tables)) if real_tables else set()
        has_derived = "<derived>" in alias_to_table.values()
        for col in tree.find_all(exp.Column):
            name = col.name.lower()
            qualifier = col.table.lower()
            if qualifier:
                table = alias_to_table.get(qualifier)
                if table is None:
                    errors.append(f"Unknown table alias '{col.table}'")
                elif table != "<derived>" and not isinstance(col.this, exp.Star) and name not in schema[table]:
                    errors.append(f"Unknown column '{col.table}.{col.name}'")
            elif isinstance(col.this, exp.Star):
                continue
            elif name not in known_columns and name not in defined_aliases and not has_derived:
                errors.append(f"Unknown column '{col.name}'")
        return sorted(set(errors))

    @staticmethod
    def _apply_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
        limit = tree.args.get("limit")
        current = limit.expression if limit is not None else None
        if isinstance(current, exp.Literal) and current.is_int and int(current.name) <= max_rows:
            return tree
        return tree.limit(max_rows, copy=False)
