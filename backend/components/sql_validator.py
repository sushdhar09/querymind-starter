import logging
from typing import Callable, Mapping, Optional, Union

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

import tsql_rules
from schemas import Guardrails, ValidatorOutput

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
        "Command", "Install", "LoadData", "Copy", "Set", "Transaction", "Commit", "Rollback", "Use", "Merge", "Analyze",
        "Execute", "Into", "Parameter", "SessionParameter",
    ) if hasattr(exp, n)
)
BLOCK_MESSAGES = {"Into": "SELECT INTO is not allowed", "Execute": "EXEC is not allowed",
                  "Parameter": "Variables (@name) are not allowed", "SessionParameter": "Variables (@@name) are not allowed"}
QUERY_ROOTS = (exp.Select, exp.Union)
ALLOWED_SCHEMA = "dbo"


class SqlValidator:
    def __init__(self, schema: Union[SchemaMap, Callable[[], SchemaMap]],
                 guardrails: Union[Guardrails, Callable[[], Guardrails], None] = None,
                 blocked_columns: Optional[Callable[[], set[str]]] = None):
        self._schema = schema
        self._guardrails = guardrails or Guardrails()
        self._blocked_columns = blocked_columns

    def _get_schema(self) -> dict[str, set[str]]:
        s = self._schema() if callable(self._schema) else self._schema
        return {t.lower(): {c.lower() for c in cols} for t, cols in s.items()}

    def _get_guardrails(self) -> Guardrails:
        return self._guardrails() if callable(self._guardrails) else self._guardrails

    def validate(self, sql: str, max_rows: int = 100, dialect: str = tsql_rules.DIALECT) -> ValidatorOutput:
        errors = self._check(sql, max_rows)
        if isinstance(errors, list):
            return ValidatorOutput(valid=False, sanitized_sql=sql, errors=errors)
        return ValidatorOutput(valid=True, sanitized_sql=errors.sql(dialect=tsql_rules.DIALECT, comments=False))

    def _check(self, sql: str, max_rows: int) -> Union[list[str], exp.Expression]:
        g = self._get_guardrails()
        try:
            tokens = tsql_rules.tokenize(sql)
            statements = [s for s in sqlglot.parse(sql, read=tsql_rules.DIALECT) if s is not None]
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
                kind = type(node).__name__
                errors.append(BLOCK_MESSAGES.get(kind, f"{kind.upper()} is not allowed"))
            for op, classes in TOGGLEABLE_OPERATIONS.items():
                if op in blocked_ops and isinstance(node, classes):
                    errors.append(f"{op} statements are blocked by guardrails")
        errors += tsql_rules.forbidden_function_errors(tokens)
        if errors:
            return sorted(set(errors))
        if is_query:
            errors = tsql_rules.token_errors(tokens)
            if errors:
                return errors

        errors = self._check_schema(tree, g, check_columns=is_query)
        if not errors and is_query and self._blocked_columns:
            errors = self._check_sensitive(tree, self._blocked_columns())
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
            db, catalog = t.args.get("db"), t.args.get("catalog")
            if name in derived and db is None:
                alias_to_table[(t.alias or name).lower()] = "<derived>"
                continue
            if catalog is not None or (db is not None and db.name.lower() != ALLOWED_SCHEMA):
                errors.append(f"Schema-qualified table '{t.sql()}' is not allowed")
            elif name in blocked_tables:
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
        if not errors:
            errors += tsql_rules.alias_errors(tree, set().union(*schema.values()) if schema else set())
        return sorted(set(errors))

    def _check_sensitive(self, tree: exp.Expression, blocked: set[str]) -> list[str]:
        if not blocked:
            return []
        schema = self._get_schema()
        alias_to_table = {(t.alias or t.name).lower(): t.name.lower() for t in tree.find_all(exp.Table)}
        used_tables = set(alias_to_table.values()) & set(schema)
        errors: set[str] = set()
        for col in tree.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                tables = [alias_to_table.get(col.table.lower())] if col.table else list(used_tables)
                for t in tables:
                    errors |= {f"{t}.{c}" for c in schema.get(t, ()) if f"{t}.{c}" in blocked}
                continue
            name = col.name.lower()
            tables = [alias_to_table.get(col.table.lower())] if col.table else [t for t in used_tables if name in schema[t]]
            errors |= {f"{t}.{name}" for t in tables if t and f"{t}.{name}" in blocked}
        for star in tree.find_all(exp.Star):
            if isinstance(star.parent, (exp.Count, exp.Column)):
                continue
            errors |= {f"{t}.{c}" for t in used_tables for c in schema[t] if f"{t}.{c}" in blocked}
        return [f"Sensitive column(s) not allowed: {sorted(errors)}. Select explicit non-sensitive columns."] if errors else []

    @staticmethod
    def _apply_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
        limit = tree.args.get("limit")
        current = limit.expression if limit is not None else None
        if isinstance(current, exp.Literal) and current.is_int and int(current.name) <= max_rows:
            return tree
        return tree.limit(max_rows, copy=False)
