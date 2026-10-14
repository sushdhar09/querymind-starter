import re
from typing import Optional

from backend.schema_index import SchemaIndex
from backend.schemas import ClassifierOutput

_VERBS = r"drop|delete|remove|erase|wipe|purge|update|modify|insert|create|truncate|alter|rename|grant|revoke"
_POLITE = r"(?:(?:please|kindly|can you|could you|would you|i want to|i need to|go ahead and|just)\s+)*"
LEADING_VERB = re.compile(rf"^\s*{_POLITE}(?:{_VERBS})\b")
SQL_FRAGMENTS = [re.compile(p) for p in (
    r"\b(?:drop|truncate|alter|create)\s+(?:table|database|schema|index|view)\b",
    r"\bdelete\s+from\b",
    r"\binsert\s+into\b",
    r"\bupdate\s+\w+\s+set\b",
    r";\s*(?:drop|delete|update|insert|alter|truncate|create)\b",
    r"\b(?:and|then|also)\s+(?:drop|delete|erase|wipe|truncate|update|insert)\b",
)]
TEMPORAL = re.compile(r"\b(?:last|past|previous|ago|today|yesterday|since|quarter|week|weeks)\b")
AGGREGATE = re.compile(r"\b(?:count|sum|avg|average|total|top|most|least|number|per|by|highest|lowest|maximum|minimum|how many)\b")


class QueryClassifier:
    def __init__(self, index: Optional[SchemaIndex] = None):
        self.index = index

    def classify(self, nl_query: str) -> ClassifierOutput:
        q = nl_query.lower()
        if LEADING_VERB.search(q) or any(p.search(q) for p in SQL_FRAGMENTS):
            return ClassifierOutput(query_type="UNSUPPORTED", reason="Only read-only analytical questions are supported")
        a = self.index.analyze(nl_query) if self.index else None
        tables, columns = (a.tables, a.columns) if a else ([], [])
        if TEMPORAL.search(q):
            t = "SELECT_TEMPORAL"
        elif AGGREGATE.search(q):
            t = "SELECT_AGGREGATE"
        elif len(tables) >= 2:
            t = "SELECT_JOIN"
        else:
            t = "SELECT_SIMPLE"
        return ClassifierOutput(query_type=t, tables_mentioned=tables, columns_mentioned=columns)
