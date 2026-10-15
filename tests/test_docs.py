import importlib.util
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from backend.config import DB_PATH

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run data/seed_db.py first")


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
    example = {line.split("=")[0] for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if "=" in line}
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


@needs_db
def test_data_dictionary_covers_every_table_and_column():
    text = (ROOT / "docs" / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if table in {"query_logs", "few_shot_examples", "schema_descriptions"}:
            assert f"`{table}`" not in text
            continue
        assert f"## `{table}`" in text
        for column in (r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')):
            assert f"`{column}`" in text, f"{table}.{column} missing from the data dictionary"
    assert "Known ambiguities" in text and "orders.total_amount" in text and "payments.amount" in text


@needs_db
def test_committed_data_dictionary_is_up_to_date():
    generated = load_generator().build(DB_PATH)
    committed = (ROOT / "docs" / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    assert committed.strip() == generated.strip(), "run: python data/generate_data_dictionary.py"


@needs_db
def test_data_dictionary_script_runs_standalone(tmp_path):
    out = tmp_path / "dict.md"
    result = subprocess.run([sys.executable, "data/generate_data_dictionary.py", "--out", str(out)], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and out.read_text(encoding="utf-8").startswith("# Data dictionary")
