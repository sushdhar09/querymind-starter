import math
from collections import defaultdict
from typing import Any

import sqlglot
from sqlglot import diff
from sqlglot.errors import SqlglotError


def _norm_value(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str):
        return v
    return v


def _row_key(row) -> tuple:
    return tuple((type(v).__name__ if v is None else "v", repr(_norm_value(v))) for v in row)


def is_ordered(sql: str, dialect: str = "sqlite") -> bool:
    try:
        return sqlglot.parse_one(sql, read=dialect).args.get("order") is not None
    except SqlglotError:
        return False


def rows_match(generated: list, truth: list, ordered: bool = False) -> bool:
    if len(generated) != len(truth):
        return False
    g = [_row_key(r) for r in generated]
    t = [_row_key(r) for r in truth]
    return g == t if ordered else sorted(g) == sorted(t)


def sql_equivalent(generated: str, truth: str, dialect: str = "sqlite") -> bool:
    if not generated or not generated.strip():
        return False
    try:
        a = sqlglot.parse_one(generated, read=dialect)
        b = sqlglot.parse_one(truth, read=dialect)
    except SqlglotError:
        return False
    if b.args.get("limit") is None:
        a = a.copy()
        a.set("limit", None)
    edits = diff(a, b, delta_only=True)
    return all(type(e).__name__ == "Move" for e in edits)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def rate(successes: int, total: int) -> dict:
    lo, hi = wilson(successes, total)
    return {"correct": successes, "total": total, "rate": successes / total if total else 0.0,
            "ci95": [round(lo, 4), round(hi, 4)]}


def breakdown(results: list[dict], key: str = "query_type") -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[r.get(key) or "unknown"].append(r)
    return {k: {"execution_accuracy": rate(sum(r["execution_match"] for r in v), len(v)),
                "exact_match": rate(sum(r["exact_match"] for r in v), len(v))}
            for k, v in sorted(groups.items())}


def weighted_score(exec_rate: float, exact_rate: float, guardrail_rate: float) -> dict:
    parts = {"execution_accuracy": 30 * exec_rate, "exact_match": 15 * exact_rate, "guardrails": 15 * guardrail_rate}
    return {"points": round(sum(parts.values()), 2), "max_points": 60, "parts": {k: round(v, 2) for k, v in parts.items()}}
