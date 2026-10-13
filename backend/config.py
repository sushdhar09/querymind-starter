from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"
DB_PATH = DATA_DIR / "querymind.db"
CHROMA_PATH = DATA_DIR / "chroma"

# Ensure data dir exists
DATA_DIR.mkdir(exist_ok=True)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock") # mock | anthropic | openai
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

GUARDRAILS = {
    "blocked_keywords": ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"],
    "max_rows": 100,
}