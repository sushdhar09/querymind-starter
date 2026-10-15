FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Microsoft ODBC Driver 18 for SQL Server (pyodbc needs it to reach Azure SQL). Must run as root, before USER below.
# This is Microsoft's Debian 12 recipe; the base image is pinned to bookworm so the repository path below matches it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# Dependencies next so this layer is cached until pyproject.toml changes.
COPY pyproject.toml ./
RUN python -c "import tomllib; print(chr(10).join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Fail the build, not the first query, if the driver is not registered.
RUN python -c "import pyodbc; d = pyodbc.drivers(); assert 'ODBC Driver 18 for SQL Server' in d, d"

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /var/lib/querymind \
    && chown app:app /var/lib/querymind
USER app

# Bake the local embedding model (about 80 MB) into the image so the container works without internet at runtime.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warm up'])"

COPY --chown=app:app backend ./backend
COPY --chown=app:app data/column_metadata.json data/examples_seed.json ./data/
COPY --chown=app:app --chmod=0755 docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh

ENV APP_DB_PATH=/var/lib/querymind/app.db \
    CHROMA_PATH=/var/lib/querymind/chroma \
    LOG_FORMAT=json \
    EMBEDDING_MODE=onnx \
    ANALYTICS_BACKEND=azure

VOLUME /var/lib/querymind
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
