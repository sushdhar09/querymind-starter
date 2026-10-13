
from types import SimpleNamespace

class ResultFormatter:
    def format(self, exec_result, nl_query):
        cols = getattr(exec_result, 'columns', [])
        rows = getattr(exec_result, 'rows', [])
        table = [dict(zip(cols, r)) for r in rows]
        summary = f"Found {len(rows)} rows for query: {nl_query[:50]}"
        return SimpleNamespace(table=table, nl_summary=summary, columns=cols, row_count=len(rows))

def format_results(exec_result, nl_query):
    return ResultFormatter().format(exec_result, nl_query)
