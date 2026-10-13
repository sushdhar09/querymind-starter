from backend.schemas import ExecutorOutput, FormatterOutput


class ResultFormatter:
    def format(self, execution: ExecutorOutput, nl_query: str) -> FormatterOutput:
        table = [dict(zip(execution.columns, r)) for r in execution.rows]
        summary = f"Found {execution.row_count} rows for query: {nl_query[:50]}"
        return FormatterOutput(table=table, nl_summary=summary, columns=execution.columns, row_count=execution.row_count)
