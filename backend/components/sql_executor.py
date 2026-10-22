import time
from typing import Optional
import tsql_rules 
from config import ABSOLUTE_MAX_ROWS, QUERY_TIMEOUT_S
from db import AnalyticsDatabase, DatabaseError
from schemas import ExecutorOutput


class SqlExecutor:
    def __init__(self, database: AnalyticsDatabase, timeout_s: Optional[float] = None, max_fetch_rows: int = ABSOLUTE_MAX_ROWS):
        self.database = database
        self.timeout_s = QUERY_TIMEOUT_S if timeout_s is None else timeout_s
        self.max_fetch_rows = max_fetch_rows

    def execute(self, sql: str, max_rows: Optional[int] = None) -> ExecutorOutput:
        start = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - start) * 1000)

        problem = tsql_rules.statement_error(sql)
        if problem:
            return ExecutorOutput(latency_ms=elapsed(), error=problem)
        limit = min(max_rows or self.max_fetch_rows, self.max_fetch_rows)
        try:
            result = self.database.query(sql, limit, self.timeout_s)
        except DatabaseError as e:
            return ExecutorOutput(latency_ms=elapsed(), error=str(e))
        return ExecutorOutput(rows=result.rows, columns=result.columns, row_count=len(result.rows), truncated=result.truncated,
                              latency_ms=elapsed())
