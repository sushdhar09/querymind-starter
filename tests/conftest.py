import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="querymind-test-")
os.environ["APP_DB_PATH"] = os.path.join(_tmp, "app.db")
os.environ["CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["EMBEDDING_MODE"] = "hash"
