import os
import tempfile

os.environ["APP_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="querymind-test-"), "app.db")
