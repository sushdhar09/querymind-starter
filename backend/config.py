import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent


def _resolve(env_name: str, default: str) -> Path:
    p = Path(os.getenv(env_name, default))
    return p if p.is_absolute() else ROOT_DIR / p


DATA_DIR = ROOT_DIR / "data"
DB_PATH = _resolve("DB_PATH", "data/querymind.db")
APP_DB_PATH = _resolve("APP_DB_PATH", "data/app.db")
CHROMA_PATH = _resolve("CHROMA_PATH", "data/chroma")

EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "onnx")  # onnx | hash (hash is for tests/CI)
QUERY_TIMEOUT_S = float(os.getenv("QUERY_TIMEOUT_S", "5"))
ABSOLUTE_MAX_ROWS = int(os.getenv("ABSOLUTE_MAX_ROWS", "10000"))

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]


def admin_api_key() -> str:
    return os.getenv("ADMIN_API_KEY", "")


def rate_limit_per_min() -> int:
    return int(os.getenv("RATE_LIMIT_PER_MIN", "60"))


LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")  # mock | openai
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))

GUARDRAILS = {
    "blocked_keywords": ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"],
    "max_rows": 100,
}
