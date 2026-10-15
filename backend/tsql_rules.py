"""T-SQL checks shared by the validator (early, actionable feedback for the model) and the in-memory test double
(which must reject what SQL Server rejects)."""
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

DIALECT = "tsql"
SQLITE_ONLY_FUNCTIONS = {"strftime", "julianday", "date", "datetime", "ifnull", "substr", "group_concat", "random", "printf",
                         "instr", "typeof", "unixepoch"}
FORBIDDEN_FUNCTIONS = {"openrowset", "openquery", "opendatasource", "xp_cmdshell", "sp_executesql", "db_name", "suser_sname",
                       "suser_name", "user_name", "host_name", "app_name", "serverproperty", "load_extension", "readfile",
                       "writefile"}
TSQL_HINTS = {
    "limit": "T-SQL has no LIMIT; use SELECT TOP n (or OFFSET ... FETCH after ORDER BY)",
    "date": "DATE is a type, not a function; use CAST(x AS DATE), DATEADD(...) or GETDATE()",
    "strftime": "use FORMAT(col, 'yyyy-MM') or YEAR()/MONTH()/DAY()",
    "julianday": "use DATEDIFF(day, start, end)",
    "datetime": "use GETDATE() or CAST(x AS DATETIME2)",
    "ifnull": "use ISNULL() or COALESCE()",
    "substr": "use SUBSTRING()",
}


def tokenize(sql: str) -> list[Token]:
    return sqlglot.tokenize(sql, read=DIALECT)


def token_errors(tokens: list[Token]) -> list[str]:
    errors = []
    if any(t.token_type == TokenType.LIMIT for t in tokens):
        errors.append("Incorrect syntax near 'LIMIT'. " + TSQL_HINTS["limit"])
    for token, following in zip(tokens, tokens[1:]):
        name = token.text.lower()
        if following.token_type == TokenType.L_PAREN and name in SQLITE_ONLY_FUNCTIONS:
            hint = TSQL_HINTS.get(name, "use the T-SQL equivalent")
            errors.append(f"'{name}' is not a recognized built-in function name. {hint}")
    return errors


def forbidden_function_errors(tokens: list[Token]) -> list[str]:
    return [f"Function {token.text} is not allowed" for token, following in zip(tokens, tokens[1:])
            if following.token_type == TokenType.L_PAREN and token.text.lower() in FORBIDDEN_FUNCTIONS]


def alias_errors(tree: exp.Expression, real_columns: set[str]) -> list[str]:
    errors = []
    for select in tree.find_all(exp.Select):
        aliases = {e.alias.lower() for e in select.expressions if isinstance(e, exp.Alias)} - real_columns
        for clause in ("where", "group", "having"):
            node = select.args.get(clause)
            for col in (node.find_all(exp.Column) if node is not None else ()):
                if not col.table and col.name.lower() in aliases:
                    errors.append(f"Invalid column name '{col.name}'. T-SQL does not allow a select alias in WHERE, GROUP BY or "
                                  "HAVING; repeat the expression instead")
    return list(dict.fromkeys(errors))


def statement_error(sql: str) -> Optional[str]:
    """None if `sql` is exactly one plain read-only query (SELECT / UNION, optionally with CTEs), else a message."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError as e:
        return f"Syntax error: {str(e).splitlines()[0]}"
    if len(statements) != 1:
        return "Exactly one SQL statement is allowed"
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        return "Only SELECT queries are allowed"
    if tree.find(exp.Into):
        return "SELECT INTO is not allowed"
    return None
