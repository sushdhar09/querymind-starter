import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from components.example_retriever import ExampleRetriever
from components.llm_generator import ConfiguredGenerator, LlmGenerator
from components.query_classifier import QueryClassifier
from components.result_formatter import LlmSummarizer, ResultFormatter
from components.schema_linker import SchemaLinker
from components.sql_executor import SqlExecutor
from components.sql_generator import SqlGenerator
from components.sql_validator import SqlValidator
from config import ANALYTICS_BACKEND, AZURE_SQL_CONNECTION_STRING
from db import AnalyticsDatabase, AzureSqlDatabase
from embeddings import get_embedding_function
from llm import build_client
from pipeline import Pipeline
from schema_index import SchemaIndex
from store import AppStore

log = logging.getLogger(__name__)


@dataclass
class App:
    store: AppStore
    database: AnalyticsDatabase
    index: SchemaIndex
    retriever: ExampleRetriever
    validator: SqlValidator
    executor: SqlExecutor
    pipeline: Pipeline


def build_database(backend: str = ANALYTICS_BACKEND, connection_string: str = AZURE_SQL_CONNECTION_STRING) -> AnalyticsDatabase:
    if backend == "azure":
        return AzureSqlDatabase(connection_string)
    if backend == "memory":
        from testing.inmemory_db import InMemoryDatabase
        log.warning("ANALYTICS_BACKEND=memory: using the in-memory TEST DOUBLE, not Azure SQL. Not for production.")
        return InMemoryDatabase()
    raise ValueError(f"Unknown ANALYTICS_BACKEND '{backend}' (expected 'azure' or 'memory')")


def build_app(store: AppStore, embedding_mode: str, database: AnalyticsDatabase, chroma_path: Optional[Path] = None) -> App:
    index = SchemaIndex(store, database)
    kwargs = {"chroma_path": chroma_path} if chroma_path else {}
    retriever = ExampleRetriever(store, embedding_function=get_embedding_function(embedding_mode), **kwargs)
    validator = SqlValidator(schema=index.table_columns, guardrails=store.get_guardrails, blocked_columns=store.sensitive_columns)
    executor = SqlExecutor(database)
    generator = ConfiguredGenerator(SqlGenerator(), LlmGenerator(build_client, store.get_model_config), store.get_model_config)
    pipeline = Pipeline(QueryClassifier(index), SchemaLinker(index), retriever, generator, validator, executor,
                        ResultFormatter(LlmSummarizer(build_client, store.get_model_config)))
    return App(store, database, index, retriever, validator, executor, pipeline)
