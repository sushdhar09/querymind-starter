from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

QueryType = Literal["SELECT_SIMPLE", "SELECT_AGGREGATE", "SELECT_JOIN", "SELECT_TEMPORAL", "UNSUPPORTED"]
Dialect = Literal["sqlite", "postgresql", "mysql"]


class QueryRequest(BaseModel):
    nl_query: str = Field(min_length=1, max_length=1000)
    clarify_ambiguities: bool = False
    column_choices: dict[str, str] = {}
    dialect: Dialect = "sqlite"
    max_rows: int = Field(default=100, ge=1, le=1000)


DESTRUCTIVE_OPERATIONS = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE"]
INTERNAL_TABLES = ["query_logs", "few_shot_examples", "schema_descriptions"]


class Guardrails(BaseModel):
    blocked_operations: list[str] = Field(default_factory=lambda: list(DESTRUCTIVE_OPERATIONS))
    max_rows: int = Field(default=100, ge=1, le=10000)
    allowed_table_prefixes: list[str] = []
    blocked_tables: list[str] = Field(default_factory=lambda: list(INTERNAL_TABLES))

    @field_validator("blocked_operations")
    @classmethod
    def _known_operations(cls, v: list[str]) -> list[str]:
        ops = list(dict.fromkeys(o.strip().upper() for o in v))
        unknown = sorted(set(ops) - set(DESTRUCTIVE_OPERATIONS))
        if unknown:
            raise ValueError(f"Unknown operations {unknown}; allowed: {DESTRUCTIVE_OPERATIONS}")
        return ops

    @field_validator("allowed_table_prefixes", "blocked_tables")
    @classmethod
    def _clean_names(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(s.strip() for s in v if s.strip()))


class ClassifierOutput(BaseModel):
    query_type: QueryType
    tables_mentioned: list[str] = []
    columns_mentioned: list[str] = []
    reason: Optional[str] = None


class Ambiguity(BaseModel):
    term: str = ""
    message: str
    candidates: list[str] = []


class ColumnContext(BaseModel):
    name: str
    type: str = ""
    description: str = ""
    synonyms: list[str] = []


class TableContext(BaseModel):
    table: str
    columns: list[ColumnContext] = []
    foreign_keys: list[str] = []


class LinkerOutput(BaseModel):
    resolved_tables: list[str] = []
    resolved_columns: list[str] = []
    ambiguities: list[Ambiguity] = []
    schema_context: list[TableContext] = []
    excluded_columns: list[str] = []
    value_hits: list[str] = []


class Example(BaseModel):
    question: str
    sql: str
    score: Optional[float] = None


class RetrieverOutput(BaseModel):
    examples: list[Example] = []


class GeneratorInput(BaseModel):
    nl_query: str
    dialect: Dialect = "sqlite"
    linker: LinkerOutput = LinkerOutput()
    examples: list[Example] = []
    feedback: Optional[str] = None
    previous_sql: Optional[str] = None


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
    truncated: bool = False
    error: Optional[str] = None


class FormatterOutput(BaseModel):
    table: list[dict[str, Any]] = []
    nl_summary: str = ""
    columns: list[str] = []
    row_count: int = 0
    truncated: bool = False


class PipelineResponse(BaseModel):
    status: Literal["success", "error", "blocked", "needs_clarification"]
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
    self_healed: bool = False
    total_latency_ms: int = 0


class ExampleIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    sql: str = Field(min_length=1, max_length=5000)
    query_type: QueryType = "SELECT_SIMPLE"


class ExampleRecord(ExampleIn):
    id: int
    created_at: str
    updated_at: str


class ColumnMetaIn(BaseModel):
    description: str = ""
    synonyms: list[str] = []
    is_sensitive: bool = False


class ColumnMeta(ColumnMetaIn):
    table_name: str
    column_name: str


class ModelConfig(BaseModel):
    provider: Literal["mock", "openai", "anthropic", "ollama"] = "mock"
    model: str = "gpt-4o-mini"
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=64, le=32000)
    dialect: Dialect = "sqlite"
    llm_summary: bool = True


class LogIn(BaseModel):
    nl_query: str
    generated_sql: str = ""
    status: str
    query_type: Optional[str] = None
    failed_stage: Optional[str] = None
    latency_ms: int = 0
    row_count: int = 0
    error: Optional[str] = None
    model: Optional[str] = None


class LogRecord(LogIn):
    id: int
    created_at: str


class SchemaColumn(BaseModel):
    name: str
    type: str = ""
    primary_key: bool = False
    description: str = ""
    synonyms: list[str] = []
    is_sensitive: bool = False


class SchemaTable(BaseModel):
    table_name: str
    columns: list[SchemaColumn]


class ModelConfigOut(ModelConfig):
    api_key_configured: bool


class LogStats(BaseModel):
    total: int = 0
    by_status: dict[str, int] = {}
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
