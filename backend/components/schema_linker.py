from typing import Optional

from backend.schema_index import SchemaIndex
from backend.schemas import LinkerOutput


class SchemaLinker:
    def __init__(self, index: SchemaIndex):
        self.index = index

    def link(self, nl_query: str, choices: Optional[dict[str, str]] = None) -> LinkerOutput:
        a = self.index.analyze(nl_query, choices)
        return LinkerOutput(resolved_tables=a.tables, resolved_columns=a.columns, ambiguities=a.ambiguities,
                            schema_context=self.index.context(a.tables), excluded_columns=a.excluded,
                            value_hits=a.value_hits)
