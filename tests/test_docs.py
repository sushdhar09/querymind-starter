import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

from backend import demo_data
from backend.testing.inmemory_db import InMemoryDatabase

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_data_dictionary", ROOT / "data" / "generate_data_dictionary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readme_relative_links_point_at_real_files():
    links = [m for m in re.findall(r"\]\(([^)]+)\)", README) if not m.startswith(("http", "#"))]
    assert links
    for link in links:
        assert (ROOT / link.split("#")[0]).exists(), f"README links to missing {link}"


def test_readme_configuration_table_matches_env_example():
    section = README.split("## Configuration")[1].split("## Using it")[0]
    documented = set()
    for row in re.findall(r"^\| (`[^|]+`) \|", section, re.MULTILINE):
        documented |= set(re.findall(r"`([A-Z_]+)`", row))
    example = {line.split("=")[0] for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if "=" in line and not line.lstrip().startswith("#")}
    assert documented <= example, f"documented but not in .env.example: {documented - example}"
    assert example <= documented, f"in .env.example but undocumented: {example - documented}"


def test_readme_mentions_only_commands_and_files_that_exist():
    for path in ["data/seed_azure.py", "data/check_azure.py", "data/generate_data_dictionary.py", "eval/evaluation_harness.py",
                 ".github/workflows/ci.yml", "docs/DATA_DICTIONARY.md", ".env.example", "docker-compose.yml", "backend/db.py",
                 "backend/demo_data.py", "backend/tsql_rules.py"]:
        assert path in README and (ROOT / path).exists(), path


def test_readme_does_not_carry_the_retired_claims():
    for stale in ["74.8", "FINAL_PASS", "run_standalone", "Screenshots for Submission", "Zip the folder", "seed_db", "DB_TYPE",
                  "SEED_ON_START", "migrate_sqlite", "mid-migration", "debug_run", "test_azure", "querymind.db"]:
        assert stale not in README, stale
    assert not re.search(r"(?<![A-Z_])DB_PATH", README), "there is no local analytics DB_PATH any more"
    assert "APP_DB_PATH" in README, "the app database is still local SQLite"


def test_readme_states_the_key_caveats():
    for caveat in ["No real-LLM verification", "memorised the test set", "Not verified against a live Azure SQL server",
                   "Docker images have not been built", "Credentials were committed", "in-memory test double", "T-SQL on Azure SQL"]:
        assert caveat in README, caveat


def test_readme_links_the_committed_evaluation_report_and_its_numbers_match_it():
    import json
    link = re.search(r"\]\((eval/reports/[^)]+\.md)\)", README).group(1)
    report = json.loads((ROOT / link).with_suffix(".json").read_text(encoding="utf-8"))
    assert report["meta"]["database_kind"] == "memory" and report["meta"]["provider"] == "mock"
    test, para, g = report["test_set"], report["paraphrase_set"], report["guardrails"]
    assert f"{test['execution_accuracy']['correct']}/{test['execution_accuracy']['total']}" in README
    assert f"{para['execution_accuracy']['correct']}/{para['execution_accuracy']['total']} (24%)" in README
    assert f"{g['compliance']['correct']}/{g['compliance']['total']}" in README
    assert f"{g['executor_only']['denied']}/{g['executor_only']['destructive_total']}" in README
    assert not g["false_positives"]["valid_sql_rejected"] and not g["false_positives"]["classified_unsupported"]
    assert f"0/{g['false_positives']['valid_sql_total']} legitimate SQL" in README and f"0/{g['false_positives']['benign_total']} benign" in README
    assert len(list((ROOT / "eval" / "reports").glob("*.md"))) == 1, "keep exactly one committed baseline"


def test_readme_numbers_about_the_demo_data_match_the_dataset():
    from backend import demo_data
    for count in demo_data.ROW_COUNTS.values():
        assert f"{count:,}" in README


def test_data_dictionary_covers_every_table_and_column():
    text = (ROOT / "docs" / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    for table in demo_data.SCHEMA:
        assert f"## `{table.name}`" in text
        for column in table.columns:
            assert f"`{column.name}`" in text, f"{table.name}.{column.name} missing from the data dictionary"
    for internal in ("query_logs", "few_shot_examples", "schema_descriptions"):
        assert f"`{internal}`" not in text
    assert "Known ambiguities" in text and "orders.total_amount" in text and "payments.amount" in text
    assert "NVARCHAR" in text and "SQLite" not in text and "T-SQL" in text


def test_committed_data_dictionary_is_up_to_date():
    generated = load_generator().build(InMemoryDatabase())
    committed = (ROOT / "docs" / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    assert committed.strip() == generated.strip(), "run: python data/generate_data_dictionary.py"


def test_data_dictionary_script_runs_standalone(tmp_path):
    out = tmp_path / "dict.md"
    env = {**os.environ, "ANALYTICS_BACKEND": "memory"}
    result = subprocess.run([sys.executable, "data/generate_data_dictionary.py", "--out", str(out)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and out.read_text(encoding="utf-8").startswith("# Data dictionary")


def test_data_dictionary_describes_the_database_it_is_given():
    class Tiny(InMemoryDatabase):
        def load_schema(self):
            schema = super().load_schema()
            schema.tables = {"orders": schema.tables["orders"]}
            schema.foreign_keys = [fk for fk in schema.foreign_keys if fk[0] == "orders"]
            return schema

    text = load_generator().build(Tiny())
    assert "## `orders`" in text and "## `customers`" not in text and "`orders.customer_id` references `customers.customer_id`" in text


def test_demo_script_quotes_the_real_check_azure_numbers():
    spec = importlib.util.spec_from_file_location("check_azure", ROOT / "data" / "check_azure.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    levels = [check.comparison_level(sql) for _, sql in check.shipped_queries()]
    demo = (ROOT / "DEMO.md").read_text(encoding="utf-8")
    claim = f"{levels.count('full')} queries by value, {levels.count('shape')} by shape, {levels.count('none')} only that they run"
    assert claim in demo and f"all {len(levels)} shipped queries" in demo


def test_demo_script_has_no_stale_sqlite_era_content():
    demo = (ROOT / "DEMO.md").read_text(encoding="utf-8")
    for stale in ("seed_db", "querymind.db", "DB_TYPE", "DB_PATH", "SEED_ON_START", "migrate_sqlite", "date('now','-30 days')", "LIMIT 100"):
        assert stale not in demo, stale
    assert "74.8%" in demo and "Never quote" in demo
