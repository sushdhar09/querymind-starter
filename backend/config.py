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

QUERY_TIMEOUT_S = float(os.getenv("QUERY_TIMEOUT_S", "5"))
ABSOLUTE_MAX_ROWS = int(os.getenv("ABSOLUTE_MAX_ROWS", "10000"))

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")  # mock | openai
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

GUARDRAILS = {
    "blocked_keywords": ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"],
    "max_rows": 100,
}
