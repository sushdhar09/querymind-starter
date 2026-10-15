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
    for path in ["data/seed_db.py", "data/generate_data_dictionary.py", "eval/evaluation_harness.py", ".github/workflows/ci.yml",
                 "docs/DATA_DICTIONARY.md", ".env.example", "docker-compose.yml"]:
        assert path in README and (ROOT / path).exists()


def test_readme_does_not_carry_the_retired_claims():
    for stale in ["74.8", "FINAL_PASS", "run_standalone", "Screenshots for Submission", "Zip the folder"]:
        assert stale not in README


def test_readme_states_the_key_caveats():
    for caveat in ["No real-LLM verification", "memorised the test set", "mid-migration", "Docker images have not been built",
                   "Credentials were committed"]:
        assert caveat in README


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
