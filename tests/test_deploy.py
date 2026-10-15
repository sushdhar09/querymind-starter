import os
import re
import sqlite3
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
    assert env["DB_PATH"].startswith("/var/lib/querymind/") and env["APP_DB_PATH"].startswith("/var/lib/querymind/")
    assert env["CHROMA_PATH"].startswith("/var/lib/querymind/")


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
    assert used <= keys and {"SEED_ON_START", "ADMIN_API_KEY", "NEXT_PUBLIC_API_URL"} <= keys


def test_seed_script_honours_db_path_and_creates_parent_dirs(tmp_path):
    target = tmp_path / "volume" / "nested" / "analytics.db"
    result = subprocess.run([sys.executable, "data/seed_db.py"], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "DB_PATH": str(target)}, timeout=120)
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5000
    conn.close()


def import_main(admin_key: str, tmp_path) -> str:
    env = {**os.environ, "ADMIN_API_KEY": admin_key, "APP_DB_PATH": str(tmp_path / "app.db"),
           "CHROMA_PATH": str(tmp_path / "chroma"), "EMBEDDING_MODE": "hash", "LOG_FORMAT": "json"}
    return subprocess.run([sys.executable, "-c", "import backend.main"], cwd=ROOT, capture_output=True, text=True,
                          env=env, timeout=120).stderr


def test_startup_warns_about_the_placeholder_admin_key(tmp_path):
    assert "placeholder 'change-me'" in import_main("change-me", tmp_path)
    assert "placeholder" not in import_main("a-real-secret-value", tmp_path / "second")
