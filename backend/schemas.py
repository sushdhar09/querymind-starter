from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

QueryType = Literal["SELECT_SIMPLE", "SELECT_AGGREGATE", "SELECT_JOIN", "SELECT_TEMPORAL", "UNSUPPORTED"]
Dialect = Literal["sqlite", "postgresql", "mysql"]


class QueryRequest(BaseModel):
    nl_query: str = Field(min_length=1, max_length=1000)
    dialect: Dialect = "sqlite"
    max_rows: int = Field(default=100, ge=1, le=1000)


DESTRUCTIVE_OPERATIONS = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE"]
INTERNAL_TABLES = ["query_logs", "few_shot_examples", "schema_descriptions"]


class Guardrails(BaseModel):
    blocked_operations: list[str] = Field(default_factory=lambda: list(DESTRUCTIVE_OPERATIONS))
    max_rows: int = Field(default=100, ge=1, le=10000)
    allowed_table_prefixes: list[str] = []
    blocked_tables: list[str] = Field(default_factory=lambda: list(INTERNAL_TABLES))


class ClassifierOutput(BaseModel):
    query_type: QueryType
    tables_mentioned: list[str] = []
    columns_mentioned: list[str] = []
    reason: Optional[str] = None


class Ambiguity(BaseModel):
    message: str
    candidates: list[str] = []


class LinkerOutput(BaseModel):
    resolved_tables: list[str] = []
    resolved_columns: list[str] = []
    ambiguities: list[Ambiguity] = []


class Example(BaseModel):
    question: str
    sql: str


class RetrieverOutput(BaseModel):
    examples: list[Example] = []


class GeneratorInput(BaseModel):
    nl_query: str
    dialect: Dialect = "sqlite"
    linker: LinkerOutput = LinkerOutput()
    examples: list[Example] = []
    feedback: Optional[str] = None


class GeneratorOutput(BaseModel):
    sql: str
    explanation: str = ""


class ValidatorOutput(BaseModel):
    valid: bool
    sanitized_sql: str = ""
    errors: list[str] = []


class ExecutorOutput(BaseModel):
    rows: list[list[Any]] = []
    columns: list[str] = []
    row_count: int = 0
    latency_ms: int = 0
    error: Optional[str] = None


class FormatterOutput(BaseModel):
    table: list[dict[str, Any]] = []
    nl_summary: str = ""
    columns: list[str] = []
    row_count: int = 0


class PipelineResponse(BaseModel):
    status: Literal["success", "error", "blocked"]
    natural_language_query: str
    query_type: Optional[QueryType] = None
    resolved_entities: LinkerOutput = LinkerOutput()
    few_shot_examples: list[Example] = []
    generated_sql: str = ""
    explanation: str = ""
    result: FormatterOutput = FormatterOutput()
    execution: ExecutorOutput = ExecutorOutput()
    error: Optional[str] = None
    failed_stage: Optional[str] = None
    total_latency_ms: int = 0
