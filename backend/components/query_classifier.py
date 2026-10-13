
class QueryClassifier:
    def classify(self, nl_query: str):
        q = nl_query.lower()
        blocked = ["drop", "delete", "update", "insert", "truncate", "alter"]
        if any(b in q for b in blocked):
            from types import SimpleNamespace
            return SimpleNamespace(query_type="UNSUPPORTED", confidence=1.0)
        if any(w in q for w in ["last", "past", "quarter", "month", "year", "days", "today", "ago"]):
            t = "SELECT_TEMPORAL"
        elif "join" in q or "with" in q and ("customer" in q or "product" in q):
            t = "SELECT_JOIN"
        elif any(w in q for w in ["count", "sum", "avg", "total", "average", "top", "most", "by"]):
            t = "SELECT_AGGREGATE"
        else:
            t = "SELECT_SIMPLE"
        from types import SimpleNamespace
        return SimpleNamespace(query_type=t, confidence=0.9)

def classify_query(nl):
    return QueryClassifier().classify(nl)
