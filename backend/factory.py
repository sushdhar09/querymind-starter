from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.components.example_retriever import ExampleRetriever
from backend.components.llm_generator import ConfiguredGenerator, LlmGenerator
from backend.components.query_classifier import QueryClassifier
from backend.components.result_formatter import LlmSummarizer, ResultFormatter
from backend.components.schema_linker import SchemaLinker
from backend.components.sql_executor import SqlExecutor
from backend.components.sql_generator import SqlGenerator
from backend.components.sql_validator import SqlValidator
from backend.config import DB_PATH
from backend.embeddings import get_embedding_function
from backend.llm import build_client
from backend.pipeline import Pipeline
from backend.schema_index import SchemaIndex
from backend.store import AppStore


@dataclass
class App:
    store: AppStore
    index: SchemaIndex
    retriever: ExampleRetriever
    validator: SqlValidator
    executor: SqlExecutor
    pipeline: Pipeline


def build_app(store: AppStore, embedding_mode: str, chroma_path: Optional[Path] = None, db_path: Path = DB_PATH) -> App:
    index = SchemaIndex(store, db_path)
    kwargs = {"chroma_path": chroma_path} if chroma_path else {}
    retriever = ExampleRetriever(store, embedding_function=get_embedding_function(embedding_mode), **kwargs)
    validator = SqlValidator(guardrails=store.get_guardrails, blocked_columns=store.sensitive_columns)
    executor = SqlExecutor(db_path)
    generator = ConfiguredGenerator(SqlGenerator(), LlmGenerator(build_client, store.get_model_config), store.get_model_config)
    pipeline = Pipeline(QueryClassifier(index), SchemaLinker(index), retriever, generator, validator, executor,
                        ResultFormatter(LlmSummarizer(build_client, store.get_model_config)))
    return App(store, index, retriever, validator, executor, pipeline)
