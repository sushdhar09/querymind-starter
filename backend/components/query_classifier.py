from backend.schemas import ClassifierOutput

DESTRUCTIVE = ["drop", "delete", "update", "insert", "truncate", "alter"]
TEMPORAL = ["last", "past", "quarter", "month", "year", "days", "today", "ago"]
AGGREGATE = ["count", "sum", "avg", "total", "average", "top", "most", "by"]


class QueryClassifier:
    def classify(self, nl_query: str) -> ClassifierOutput:
        q = nl_query.lower()
        if any(b in q for b in DESTRUCTIVE):
            return ClassifierOutput(query_type="UNSUPPORTED", reason="Only read-only analytical questions are supported")
        if any(w in q for w in TEMPORAL):
            t = "SELECT_TEMPORAL"
        elif "join" in q or ("with" in q and ("customer" in q or "product" in q)):
            t = "SELECT_JOIN"
        elif any(w in q for w in AGGREGATE):
            t = "SELECT_AGGREGATE"
        else:
            t = "SELECT_SIMPLE"
        return ClassifierOutput(query_type=t)
