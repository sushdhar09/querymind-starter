import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
backend_dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
frontend_dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
entrypoint = (ROOT / "docker" / "entrypoint.sh").read_bytes()


def copy_sources(dockerfile: str) -> list[str]:
    sources = []
    for line in dockerfile.splitlines():
        if line.startswith("COPY") and "--from" not in line:
            parts = [p for p in line.split()[1:] if not p.startswith("--")]
            sources += parts[:-1]
    return sources


def test_compose_defines_both_services_with_persistence_and_ordering():
    backend, frontend = compose["services"]["backend"], compose["services"]["frontend"]
    assert backend["ports"] == ["8000:8000"] and frontend["ports"] == ["3000:3000"]
    assert "querymind-data:/var/lib/querymind" in backend["volumes"] and "querymind-data" in compose["volumes"]
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"
    assert backend["restart"] == frontend["restart"] == "unless-stopped"


def test_compose_pins_state_paths_so_a_copied_env_cannot_move_them():
    env = compose["services"]["backend"]["environment"]
    assert env["APP_DB_PATH"].startswith("/var/lib/querymind/") and env["CHROMA_PATH"].startswith("/var/lib/querymind/")
    assert "DB_PATH" not in env and "SEED_ON_START" not in env, "there is no local analytics database any more"
    assert env["ANALYTICS_BACKEND"].startswith("${ANALYTICS_BACKEND:-azure}")


def test_compose_requires_an_admin_key_and_hardcodes_no_secrets():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${ADMIN_API_KEY:?" in text
    assert not re.search(r"sk-[A-Za-z0-9]|API_KEY: *[A-Za-z0-9]{8,}", text)
    assert (compose["services"]["backend"]["env_file"][0]["required"]) is False


def test_compose_services_are_hardened():
    for svc in compose["services"].values():
        assert "no-new-privileges:true" in svc["security_opt"] and svc["cap_drop"] == ["ALL"]


def test_frontend_api_url_is_a_build_arg():
    args = compose["services"]["frontend"]["build"]["args"]
    assert "NEXT_PUBLIC_API_URL" in args and "ARG NEXT_PUBLIC_API_URL" in frontend_dockerfile


@pytest.mark.parametrize("dockerfile,context", [(backend_dockerfile, ROOT), (frontend_dockerfile, ROOT / "frontend")])
def test_dockerfiles_copy_only_paths_that_exist(dockerfile, context):
    sources = copy_sources(dockerfile)
    assert sources
    for src in sources:
        if src != ".":
            assert (context / src).exists(), f"{src} is not in the build context"


@pytest.mark.parametrize("dockerfile", [backend_dockerfile, frontend_dockerfile])
def test_dockerfiles_run_as_non_root_with_healthcheck(dockerfile):
    assert re.search(r"^USER (?!root)\w+", dockerfile, re.MULTILINE) and "HEALTHCHECK" in dockerfile


def test_backend_image_does_not_bake_in_secrets_or_state():
    assert "ADMIN_API_KEY" not in backend_dockerfile and "OPENAI" not in backend_dockerfile
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for pattern in (".env", "*.db", ".git", "tests", "frontend", ".venv"):
        assert pattern in ignored


def test_backend_dependencies_install_from_pyproject_without_torch():
    import tomllib
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert deps and not any("torch" in d or "sentence" in d for d in deps)


def test_entrypoint_is_posix_safe():
    assert entrypoint.startswith(b"#!/bin/sh\n") and b"\r" not in entrypoint
    assert b"set -eu" in entrypoint and b'exec "$@"' in entrypoint
    assert "eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_next_standalone_output_is_opt_in():
    config = (ROOT / "frontend" / "next.config.js").read_text(encoding="utf-8")
    assert "NEXT_OUTPUT === 'standalone'" in config and "NEXT_OUTPUT=standalone" in frontend_dockerfile
    assert ".next/standalone" in frontend_dockerfile and ".next/static" in frontend_dockerfile


def test_env_example_documents_every_variable_the_deployment_uses():
    keys = {line.split("=")[0] for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if "=" in line and not line.lstrip().startswith("#")}
    used = set(re.findall(r"\$\{(\w+)[:?-]", (ROOT / "docker-compose.yml").read_text(encoding="utf-8")))
    assert used <= keys and {"ADMIN_API_KEY", "NEXT_PUBLIC_API_URL", "ANALYTICS_BACKEND", "AZURE_SQL_CONNECTION_STRING"} <= keys
    assert not keys & {"DB_PATH", "SEED_ON_START", "DB_TYPE"}


def import_main(admin_key: str, tmp_path) -> str:
    env = {**os.environ, "ADMIN_API_KEY": admin_key, "APP_DB_PATH": str(tmp_path / "app.db"),
           "CHROMA_PATH": str(tmp_path / "chroma"), "EMBEDDING_MODE": "hash", "LOG_FORMAT": "json"}
    return subprocess.run([sys.executable, "-c", "import backend.main"], cwd=ROOT, capture_output=True, text=True,
                          env=env, timeout=120).stderr


def test_startup_warns_about_the_placeholder_admin_key(tmp_path):
    assert "placeholder 'change-me'" in import_main("change-me", tmp_path)
    assert "placeholder" not in import_main("a-real-secret-value", tmp_path / "second")


def test_odbc_driver_is_installed_as_root_before_dropping_privileges():
    lines = backend_dockerfile.splitlines()
    apt = next(i for i, line in enumerate(lines) if "apt-get install" in line)
    user = next(i for i, line in enumerate(lines) if line.startswith("USER "))
    assert apt < user, "apt-get cannot run after USER app"
    assert "msodbcsql18" in backend_dockerfile and "ACCEPT_EULA=Y" in backend_dockerfile
    assert "apt-key" not in backend_dockerfile, "apt-key is deprecated and not on current Debian"
    assert "gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg" in backend_dockerfile
    assert "unixodbc-dev" not in backend_dockerfile and "rm -rf /var/lib/apt/lists/*" in backend_dockerfile


def test_base_image_matches_the_microsoft_repository_it_installs_from():
    assert re.search(r"^FROM python:3\.12-slim-bookworm$", backend_dockerfile, re.MULTILINE)
    assert "packages.microsoft.com/config/debian/12/prod.list" in backend_dockerfile


def test_the_build_fails_if_the_driver_is_not_registered_and_the_name_matches_the_connection_string():
    assert "'ODBC Driver 18 for SQL Server' in d" in backend_dockerfile
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "Driver={ODBC Driver 18 for SQL Server}" in example


def test_image_runs_against_azure_by_default_and_carries_no_sqlite_seeding():
    assert "ANALYTICS_BACKEND=azure" in backend_dockerfile
    for gone in ("seed_db", "schema.sql", "SEED_ON_START", "querymind.db"):
        assert gone not in backend_dockerfile, gone
    assert not re.search(r"(?<![A-Z_])DB_PATH", backend_dockerfile), "APP_DB_PATH is fine; a local analytics DB_PATH is not"
    assert "data/column_metadata.json" in backend_dockerfile and "data/examples_seed.json" in backend_dockerfile


def test_removed_sqlite_and_scratch_files_are_gone():
    for gone in ("data/seed_db.py", "data/migrate_sqlite_to_azure.py", "test_azure.py", "debug_run.py"):
        assert not (ROOT / gone).exists(), gone


def test_no_real_credentials_are_committed():
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    tracked = subprocess.run([git, "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.split()
    skip = ("tests/", "uv.lock", "package-lock.json", "eval/reports/", "docs/")
    leaks = []
    for name in tracked:
        path = ROOT / name
        if name.startswith(skip) or not path.is_file() or path.suffix in (".png", ".ico", ".db"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in re.finditer(r"(?i)(pwd|password)=([^;\s'\"]+)", text):
            if m.group(2).upper() not in {"YOUR_PASSWORD", "STRONG_PASSWORD", "..."} and not m.group(2).startswith(("$", "{", "<")):
                leaks.append(f"{name}: {m.group(1)}=...")
    assert not leaks, leaks


def test_pyproject_has_only_what_the_code_uses():
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = [d.split(">")[0].split("[")[0] for d in project["project"]["dependencies"]]
    assert "pyodbc" in deps and "sqlalchemy" not in deps and len(deps) == len(set(deps))
    assert "backend.testing" in project["tool"]["setuptools"]["packages"]


def test_ci_builds_and_smoke_tests_the_images_and_no_longer_seeds_sqlite():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "seed_db" not in ci
    assert "docker build -t querymind-backend ." in ci and "docker build -t querymind-frontend frontend" in ci
    assert "ODBC Driver 18 for SQL Server" in ci and "ANALYTICS_BACKEND=memory" in ci and "docker compose config --quiet" in ci
    assert "ruff check ." in ci and "pytest" in ci


def test_the_model_config_page_offers_only_tsql():
    page = (ROOT / "frontend" / "app" / "admin" / "model-config" / "page.jsx").read_text(encoding="utf-8")
    for gone in ("sqlite", "postgresql", "mysql", "SQLite", "PostgreSQL", "MySQL"):
        assert gone not in page
    assert "T-SQL (Azure SQL)" in page and "dialect: 'tsql'" in page


def test_backend_config_no_longer_exposes_a_local_analytics_database():
    import backend.config as config
    assert not hasattr(config, "DB_PATH") and not hasattr(config, "DB_TYPE") and config.ANALYTICS_BACKEND in ("azure", "memory")


SH = shutil.which("sh")


def run_entrypoint(env: dict, *command: str):
    clean = {k: v for k, v in os.environ.items() if k not in ("ANALYTICS_BACKEND", "AZURE_SQL_CONNECTION_STRING")}
    return subprocess.run([SH, str(ROOT / "docker" / "entrypoint.sh"), *command], capture_output=True, text=True,
                          env={**clean, **env}, timeout=30)


@pytest.mark.skipif(SH is None, reason="no POSIX sh available")
def test_entrypoint_refuses_to_start_azure_mode_without_a_connection_string():
    for env in ({}, {"ANALYTICS_BACKEND": "azure"}, {"ANALYTICS_BACKEND": "azure", "AZURE_SQL_CONNECTION_STRING": ""}):
        result = run_entrypoint(env, "echo", "started")
        assert result.returncode == 1 and "AZURE_SQL_CONNECTION_STRING is not set" in result.stderr and "started" not in result.stdout


@pytest.mark.skipif(SH is None, reason="no POSIX sh available")
def test_entrypoint_starts_with_a_connection_string_and_never_prints_it():
    secret = "Pwd=entrypoint-secret-value"
    result = run_entrypoint({"AZURE_SQL_CONNECTION_STRING": f"Driver=x;{secret};"}, "echo", "started")
    assert result.returncode == 0 and result.stdout.strip() == "started"
    assert "entrypoint-secret-value" not in result.stdout + result.stderr


@pytest.mark.skipif(SH is None, reason="no POSIX sh available")
def test_entrypoint_memory_mode_warns_and_unknown_modes_fail():
    ok = run_entrypoint({"ANALYTICS_BACKEND": "memory"}, "echo", "started")
    assert ok.returncode == 0 and ok.stdout.strip() == "started" and "not Azure SQL" in ok.stderr
    bad = run_entrypoint({"ANALYTICS_BACKEND": "sqlite"}, "echo", "started")
    assert bad.returncode == 1 and "Unknown ANALYTICS_BACKEND 'sqlite'" in bad.stderr
