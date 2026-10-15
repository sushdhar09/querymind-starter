import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="querymind-test-")
os.environ["APP_DB_PATH"] = os.path.join(_tmp, "app.db")
os.environ["CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["EMBEDDING_MODE"] = "hash"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["RATE_LIMIT_PER_MIN"] = "0"
os.environ["ANALYTICS_BACKEND"] = "memory"
