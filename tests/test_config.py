import pytest
from dotenv import dotenv_values

from backend.db import AzureSqlDatabase
from backend.factory import build_app, build_database
from backend.store import AppStore
from backend.testing.inmemory_db import InMemoryDatabase

CONNECTION = ("Driver={ODBC Driver 18 for SQL Server};Server=tcp:srv.database.windows.net,1433;Database=d;Uid=u;Pwd=p@ss;"
              "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;")


def test_dotenv_reads_an_odbc_connection_string_exactly_when_it_is_quoted(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f'ANALYTICS_BACKEND=azure\n# a comment on its own line\nAZURE_SQL_CONNECTION_STRING="{CONNECTION}"\nLOG_LEVEL=INFO\n', encoding="utf-8")
    values = dotenv_values(env)
    assert values["AZURE_SQL_CONNECTION_STRING"] == CONNECTION and values["ANALYTICS_BACKEND"] == "azure"


def test_dotenv_treats_a_glued_comment_as_part_of_the_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANALYTICS_BACKEND=azure# azure | memory\n", encoding="utf-8")
    assert dotenv_values(env)["ANALYTICS_BACKEND"] == "azure# azure | memory", "this is why .env.example keeps comments on their own line"
    with pytest.raises(ValueError, match="Unknown ANALYTICS_BACKEND"):
        build_database("azure# azure | memory")


def test_env_example_has_no_glued_comments_and_defaults_to_azure():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    values = dotenv_values(root / ".env.example")
    for key, value in values.items():
        assert value is None or "#" not in value, f"{key} has a comment glued to its value"
    assert values["ANALYTICS_BACKEND"] == "azure"


def test_build_database_selects_the_backend():
    azure = build_database("azure", CONNECTION)
    assert isinstance(azure, AzureSqlDatabase) and azure._connection_string == CONNECTION and azure.dialect == "tsql"
    assert isinstance(build_database("memory"), InMemoryDatabase)


def test_building_an_azure_database_never_connects():
    build_database("azure", "")


def test_the_memory_backend_warns_loudly(caplog):
    caplog.set_level("WARNING")
    build_database("memory")
    assert any("TEST DOUBLE" in r.message for r in caplog.records)


def test_build_app_wires_the_validator_to_the_live_schema(tmp_path):
    calls = []

    class Spy(InMemoryDatabase):
        def load_schema(self):
            calls.append(1)
            return super().load_schema()

    app = build_app(AppStore(tmp_path / "app.db").init(), "hash", Spy(), tmp_path / "chroma")
    assert app.validator.validate("SELECT status FROM orders").valid and not app.validator.validate("SELECT nope FROM orders").valid
    assert calls, "the validator must learn the schema from the database through the index"
    assert app.executor.database is app.database and app.index.database is app.database
