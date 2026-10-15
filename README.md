# QueryMind AI

Ask questions about a business database in plain English. QueryMind writes the SQL, checks that it is safe, runs it on a read-only connection and answers with a table, a chart and a short summary. It includes an admin console for curating the schema descriptions, few-shot examples, guardrails and model settings.

- **Backend:** FastAPI, sqlglot, ChromaDB, with **Azure SQL** (via `pyodbc`, selected with `DB_TYPE=azuresql`) or SQLite as the analytics database. **Frontend:** Next.js.
- **LLM providers:** OpenAI, Anthropic, Ollama, or an offline rule-based `mock` provider (the default).
- **Tests:** 17 frontend tests pass. The backend suite is currently **264 passed, 5 failed, 30 errors, 1 skipped**; the failures all trace back to the Azure SQL change (see [Current status](#current-status)). Before that change it was 299 passed.
- **Honest status:** the pipeline, safety layers, admin console and evaluation harness are implemented. The LLM paths have only been exercised with fake clients, the Azure SQL path has not been verified end to end by the author of this document, and the Docker images have not been built. See [Current status](#current-status) and [Known limitations](#known-limitations) before relying on any result.

## Current status

The most recent change replaced SQLite with Azure SQL as the intended analytics database. It works for its author's Azure setup but left the repository in an inconsistent state. What is true today, measured on a clean checkout with the default SQLite settings:

| Area | State |
|---|---|
| Azure SQL (`DB_TYPE=azuresql`) | Implemented in `backend/db.py`, `sql_executor.py` and `schema_index.py`. Not verified here: no Azure credentials were used from this environment |
| SQLite seeding (`data/seed_db.py`) | **Broken.** `data/schema.sql` is now T-SQL (`IDENTITY`, `NVARCHAR(MAX)`), which SQLite rejects, so a fresh checkout cannot create the demo database. This also breaks the Docker entrypoint's first-start seed |
| Backend tests | 5 failures and 30 errors, all from the T-SQL schema being loaded into SQLite or from tests that depend on the seeded database |
| Lint (`ruff check .`) | 12 findings, so the CI lint step would fail |
| `pyodbc` | Now imported unconditionally, so it is required even in SQLite mode (on Linux it also needs the `unixodbc` system library) |
| Mock provider | Its templates now emit T-SQL (`DATEADD`, `GETDATE`). Against SQLite the evaluation fell from 25/25 to **17/25 (68%)**, all temporal questions plus two others |
| Dockerfile | A step that installs the Microsoft ODBC driver runs after `USER app`, so `apt-get` will lack permission; likely to fail at build (never built) |
| Committed credentials | `.env.example` and `test_azure.py` contained what appears to be a real Azure SQL login. `.env.example` now holds placeholders, but the old values remain in git history on the remote: **rotate that password** |

Choose one direction before building on this: keep both databases (a SQLite schema and a T-SQL schema, with dialect-aware templates) or commit to Azure SQL only and retire the SQLite paths and tests.

## Contents

[Current status](#current-status) | [Domain and data](#domain-and-data) | [How it works](#how-it-works) | [Quick start](#quick-start) | [Configuration](#configuration) | [Using it](#using-it) | [Safety model](#safety-model) | [Evaluation](#evaluation) | [Development](#development) | [Project layout](#project-layout) | [Assignment coverage](#assignment-coverage) | [Known limitations](#known-limitations)

## Domain and data

**Why e-commerce:** everyone understands it, and it naturally contains every pattern the assignment asks for: many-to-many joins (orders and products through order items), aggregates (revenue by category, orders by status), several date fields for temporal questions, and a real ambiguity ("revenue" can mean the order total or the money actually captured).

| Table | Rows | Table | Rows |
|---|---|---|---|
| customers | 1,000 | order_items | 15,000 |
| products | 200 | payments | 5,000 |
| orders | 5,000 | reviews | 3,000 |

The data is synthetic. It was seeded deterministically into SQLite by `data/seed_db.py` and can be moved to Azure SQL with `data/migrate_sqlite_to_azure.py` (copies the six tables using the admin connection string). `data/schema.sql` is now written in T-SQL for Azure SQL; `data/seed_azure.py` runs it against the server (run it from inside `data/`, because it opens `schema.sql` by relative path). The full schema, relationships, value lists and the revenue ambiguity are documented in the [data dictionary](docs/DATA_DICTIONARY.md) (regenerate it with `python data/generate_data_dictionary.py`, which reads the SQLite database).

## How it works

```
question -> Classifier -> Schema Linker -> Example Retriever -> SQL Generator -> SQL Validator -> SQL Executor -> Result Formatter
                                                                                      |  one retry with the error fed back
                                                                                      +--> (executor error: one self-healing retry)
```

| Stage | What it does | Code |
|---|---|---|
| Classifier | Detects destructive requests and labels the question simple, aggregate, join or temporal; reports tables and columns mentioned | `backend/components/query_classifier.py` |
| Schema Linker | Introspects the live schema, merges admin descriptions and synonyms, finds values such as city names by scanning low-cardinality columns, flags ambiguous terms, builds the schema context (with join paths and foreign keys) for the model, and excludes sensitive columns | `schema_linker.py`, `backend/schema_index.py` |
| Example Retriever | Embeds the question with a local MiniLM model and returns the 3 most similar curated examples from ChromaDB; the index re-syncs automatically when examples change | `example_retriever.py` |
| SQL Generator | `mock`: rule-based templates. Real providers: a prompt built from the schema context, detected values, ambiguities and examples; expects JSON back and can refuse with an explanation | `sql_generator.py`, `llm_generator.py`, `backend/llm.py` |
| SQL Validator | Parses with sqlglot and enforces the safety rules below | `sql_validator.py` |
| SQL Executor | SQLite: read-only connection with timeout, row cap and an authorizer. Azure SQL: a `pyodbc` connection after translating the SQL from SQLite dialect to T-SQL with regular expressions (`LIMIT` to `TOP` or `OFFSET ... FETCH`, `DATE('now')`, some `strftime`); relies on a read-only database login | `sql_executor.py`, `backend/db.py` |
| Result Formatter | Table plus a 1 to 2 sentence summary (LLM, falling back to a deterministic summary) | `result_formatter.py` |

Every stage exchanges typed Pydantic models (`backend/schemas.py`), is independently callable, and any failure returns a clear status (`blocked`, `error` or `needs_clarification`) instead of a fallback query. `backend/pipeline.py` wires the stages together and `backend/factory.py` assembles the real components.

Two databases are used: the analytics database (Azure SQL when `DB_TYPE=azuresql`, otherwise `data/querymind.db`; read-only for the executor) and an app database (`data/app.db`, always SQLite) for examples, column metadata, query logs and settings, with versioned migrations.

## Quick start

### Docker (never built, see [Current status](#current-status))

```bash
cp .env.example .env      # set ADMIN_API_KEY to a real secret; add OPENAI_API_KEY if you want a real LLM
docker compose up --build
```

Frontend: http://localhost:3000. API docs: http://localhost:8000/docs. App data persists in the `querymind-data` volume. The image now installs the Microsoft ODBC Driver 18, but that step sits after `USER app` in the Dockerfile and will fail for lack of permissions until it is moved above it. The first-start demo seed (`SEED_ON_START=true`) uses `data/seed_db.py`, which cannot currently read the T-SQL `schema.sql`, so for now point the container at Azure SQL (`DB_TYPE=azuresql` plus the connection string) and set `SEED_ON_START=false`.

### Azure SQL

Install the [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) and put these in `.env` (never commit them):

```
DB_TYPE=azuresql
AZURE_SQL_CONNECTION_STRING="Driver={ODBC Driver 18 for SQL Server};Server=tcp:YOUR_SERVER.database.windows.net,1433;Database=YOUR_DATABASE;Uid=YOUR_READONLY_USER;Pwd=YOUR_PASSWORD;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
```

- Use a **read-only** database user for the app. In Azure mode the executor does not apply SQLite's authorizer, `query_only` or progress-handler timeout; the validator, a "SELECT or WITH only" check and the database permissions are what protect the data.
- Create the tables with `data/seed_azure.py` and copy existing data with `data/migrate_sqlite_to_azure.py` (needs `AZURE_SQL_ADMIN_CONNECTION_STRING` and an existing `data/querymind.db`).
- The connection string is read straight from `.env` by the code (python-dotenv struggles with the braces), so keep it on one line and put comments on their own lines, never after a value.
- `debug_run.py` starts the API without auto-reload, and `.vscode/launch.json` has matching launch configurations that load `.env`.

### Local development

Requires Python 3.12+ and Node 20+.

```powershell
# Backend (Windows PowerShell; on macOS/Linux use: python3 -m venv .venv && source .venv/bin/activate)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env                  # set ADMIN_API_KEY
python data/seed_db.py                  # SQLite only; currently fails because schema.sql is T-SQL (see Current status)
uvicorn backend.main:app --reload --port 8000

# Frontend (second terminal; on Windows PowerShell use npm.cmd if script execution is disabled)
cd frontend
npm install
npm run dev                             # http://localhost:3000
```

The example retriever downloads an ~80 MB embedding model the first time it is used, even with the `mock` provider (cached in `~/.cache/chroma`). Set `EMBEDDING_MODE=hash` to skip it (lower retrieval quality, useful for tests and CI).

## Configuration

Copy `.env.example` to `.env`. Everything has a default except `ADMIN_API_KEY`.

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_API_KEY` | none | Required for every `/admin/*` route; those routes return 503 if it is unset. The server warns if it is still `change-me` |
| `LLM_PROVIDER` / `LLM_MODEL` | `mock` / `gpt-4o-mini` | Initial provider and model (changeable later in the admin UI) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL` | empty | Provider credentials; keys never leave the server |
| `LLM_TIMEOUT_S` | 30 | Per LLM request |
| `DB_TYPE` | `sqlite` | `sqlite` or `azuresql`: which analytics database the executor and schema loader use |
| `AZURE_SQL_CONNECTION_STRING` | empty | ODBC connection string for Azure SQL (use a read-only login) |
| `AZURE_SQL_ADMIN_CONNECTION_STRING` | empty | Admin login, used only by `data/migrate_sqlite_to_azure.py` |
| `DB_PATH`, `APP_DB_PATH`, `CHROMA_PATH` | `data/...` | SQLite analytics DB (SQLite mode), app DB and vector index locations |
| `EMBEDDING_MODE` | `onnx` | `onnx` (local MiniLM) or `hash` (offline, for tests) |
| `QUERY_TIMEOUT_S`, `ABSOLUTE_MAX_ROWS` | 5, 10000 | Executor limits |
| `CORS_ORIGINS` | localhost:3000 | Allowed browser origins (no wildcard) |
| `RATE_LIMIT_PER_MIN` | 60 | Per client IP for `/query`; 0 disables |
| `LOG_FORMAT`, `LOG_LEVEL` | `text`, `INFO` | `json` for one structured object per line |
| `SEED_ON_START` | true | Docker only: seed the demo DB if missing |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Compiled into the frontend; must be the address the browser uses for the API |

## Using it

### Chat

Open http://localhost:3000. With the default `mock` provider these questions work out of the box:

- Show all customers from Mumbai
- Count orders by status
- Top 5 customers by total amount spent
- Total revenue per product category
- Orders in last 30 days
- Count of successful payments by method
- Show revenue by month (asks whether you mean order totals or captured payments)
- Drop the customers table (blocked, nothing is generated)

The mock provider only understands phrasings its templates were written for; "which clients live in delhi" returns a clean error. Set `LLM_PROVIDER=openai` and an API key (or choose the provider in the Model panel) to answer free-form questions.

Results show a sortable, paged table, an automatic bar or line chart for two-column numeric results, the generated SQL, an optional "Explain" view, and a clarification prompt when a term is ambiguous. A mic button uses the browser speech API where available.

### Admin console (http://localhost:3000/admin/...)

Enter the admin key once per browser tab (kept in `sessionStorage`, never bundled).

| Panel | What you can do |
|---|---|
| Schema Manager | Edit column descriptions and synonyms; mark columns sensitive (hidden from the LLM and blocked in queries) |
| Examples Manager | Add, edit, delete few-shot examples (SQL is validated first); filter; semantic search with similarity scores |
| Query Logs | Stats, filters, pagination, CSV export (formula-injection safe) |
| Guardrails | Choose blocked SQL operations, max rows, allowed table prefixes, blocked tables; applies to the next query |
| Model Config | Provider, model, temperature, max tokens, dialect, LLM summaries on or off |

### API

Interactive docs at `/docs`. Every route except `/health` and `/ready` is also served under `/api/v1` (the frontend uses that prefix).

| Method and path | Auth | Purpose |
|---|---|---|
| `POST /query` | none (rate limited) | `{"nl_query": "...", "max_rows": 100, "clarify_ambiguities": false, "column_choices": {}}` |
| `GET /health`, `GET /ready` | none | Liveness; readiness with database and provider checks |
| `GET /admin/schema`, `PUT /admin/schema/{table}/{column}` | key | Schema browsing and metadata edits |
| `GET, POST /admin/examples`, `PUT, DELETE /admin/examples/{id}`, `GET /admin/examples/search?q=` | key | Example CRUD and semantic search |
| `GET /admin/logs`, `GET /admin/logs/export`, `GET /admin/stats` | key | Logs (with `X-Total-Count`), CSV export, summary stats |
| `GET, PUT /admin/guardrails`, `GET, PUT /admin/config` | key | Guardrails and model settings |

Send the key as `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Every response carries `X-Request-ID`, which also appears in the logs and in the query log table.

```bash
curl -s localhost:8000/query -H 'Content-Type: application/json' -d '{"nl_query":"Count orders by status"}'
curl -s localhost:8000/admin/stats -H 'X-API-Key: change-me'
```

## Safety model

A destructive statement has to get past several independent layers:

1. **Classifier** rejects destructive requests (leading verbs such as delete or drop, SQL fragments, stacked commands). It is a heuristic and is not relied on alone.
2. **Validator** (sqlglot AST, not string matching): exactly one `SELECT` or `UNION`; no DML, DDL, `PRAGMA`, `ATTACH`, `VACUUM`, transactions or dangerous functions anywhere in the tree (including inside CTEs); every table, column and alias must exist; internal tables, `sqlite_master` and schema-qualified names are blocked; sensitive columns are blocked even through `SELECT *`; `LIMIT` is added or lowered to the guardrail maximum; comments are stripped. Toggling an operation off in the Guardrails panel lets it past this layer only.
3. **Executor:** in SQLite mode a read-only connection with `query_only`, an authorizer that denies everything except reads, a progress-handler timeout and a row cap. In Azure SQL mode only the "must start with SELECT or WITH" check, the row cap and the permissions of the database login apply, so use a read-only login. The guardrail evidence below was gathered in SQLite mode only.
4. **Prompt hygiene:** the question is wrapped in tags and treated as data; sensitive columns are never in the prompt.
5. **Service level:** admin routes need a key and fail closed, CORS is an allow-list, `/query` is rate limited, logs never contain keys, query strings or question text, and unexpected errors return a generic body with a request ID.

## Evaluation

```bash
python eval/evaluation_harness.py                      # offline mock provider
python eval/evaluation_harness.py --provider openai    # needs OPENAI_API_KEY
```

The harness runs the real pipeline in-process, never substitutes fallback SQL, and exits non-zero if a gate fails (execution accuracy below 50%, any guardrail miss, the database file changing, false positives). Reports are written to `eval/reports/` as Markdown and JSON with the commit hash and data hashes.

The harness is SQLite-only (it hashes the database file and runs against `data/querymind.db`), so it cannot evaluate an Azure SQL deployment yet.

Committed baseline, **`mock` provider**, measured **before the Azure SQL change** ([full report](eval/reports/eval_mock_20261014_114445.md)). Re-running it after the change gives 17/25 (68%) on the original wording because the mock templates now emit T-SQL that SQLite cannot run (guardrails 89/89 and the 24% paraphrase score are unchanged):

| Measure | Result |
|---|---|
| Execution accuracy, original 25 questions | 25/25 (95% CI 87-100%) |
| Exact match (sqlglot AST diff) | 25/25 |
| Execution accuracy, same questions reworded | **6/25 (24%)** |
| Guardrails (40 natural-language attacks and 49 attack SQL statements) | 89/89; executor alone denies 36/36 destructive statements; database file unchanged |
| False positives | 0/12 benign questions, 0/67 legitimate SQL statements |

**Read the first rows with care.** The templates were written while looking at those 25 questions, so 100% says nothing about generalisation; the reworded set (24%) is the honest signal for `mock`. A meaningful accuracy number needs a run with a real LLM, which has not been done. With 25 questions the confidence intervals are wide.

## Development

```bash
python -m pytest -q          # currently 264 passed, 5 failed, 30 errors (see Current status); needs a seeded data/querymind.db
ruff check .                 # lint
cd frontend && npm test && npm run build
```

GitHub Actions (`.github/workflows/ci.yml`) runs lint, the backend tests, the evaluation gates and the frontend tests and build; Dependabot proposes weekly dependency updates. Before the Azure SQL change every step passed locally; with it, CI is expected to fail (lint findings, no way to seed SQLite from the T-SQL schema, and `pyodbc` needing the `unixodbc` system library on the Ubuntu runner). It has not run on GitHub yet.

## Project layout

```
backend/            FastAPI app, pipeline, typed schemas, components/, store, security, observability, db.py (SQLite/Azure connection helper)
data/               schema.sql (T-SQL), seed_db.py (SQLite), seed_azure.py, migrate_sqlite_to_azure.py, seed JSON (column metadata, 42 examples), data dictionary generator
docs/               DATA_DICTIONARY.md
eval/               evaluation harness, metrics, test/paraphrase/guardrail sets, committed reports
frontend/           Next.js chat UI and admin panels
tests/              backend tests
docker/, Dockerfile, docker-compose.yml, .github/, .vscode/launch.json, debug_run.py
test_azure.py       scratch connectivity script (uses pymssql, which is not a project dependency; contains a hard-coded login that should be removed)
DEMO.md             step-by-step demo script and status table
```

## Assignment coverage

| Deliverable | Status |
|---|---|
| Text-to-SQL pipeline, all components, independently callable with typed schemas | Done |
| Database, seed script, data dictionary | Synthetic data and data dictionary done. The seed script no longer works since `schema.sql` became T-SQL; Azure SQL setup uses `seed_azure.py` or the migration script |
| FastAPI backend with `/query` and the admin routes | Done |
| Next.js chat UI and five admin panels with persisted CRUD | Done; verified by build, unit tests and manual checks, no browser test suite |
| 30+ curated few-shot examples | 42, all verified to validate and return rows, none overlapping the evaluation questions |
| Evaluation report on a 25-query set | Harness and mock baseline done; **no real-LLM run yet** |
| README | This file |

Bonus items: ambiguity resolution (done), self-healing SQL (done), chart auto-generation (done), voice input (done), query explanation (partial: shows the model's explanation and what was resolved, not a generated plain-English breakdown of the SQL). Not done: sqlglot dialect transpilation, semantic caching.

## Known limitations

- **No real-LLM verification.** The OpenAI, Anthropic and Ollama code paths are tested against fakes only. Accuracy with a real model is unmeasured.
- **The mock provider memorised the test set** (100% on the original wording, 24% reworded). It exists for offline demos and tests.
- **Two databases, one dialect.** Only SQLite and Azure SQL exist. The model prompt, the validator and the evaluation set are still written for the SQLite dialect, and Azure queries are adapted by regular-expression translation in the executor; any SQLite construct the translator does not cover (for example `julianday`, other `strftime` formats, `LIMIT` inside subqueries) will fail on Azure SQL. PostgreSQL and MySQL are not supported.
- **The repository is mid-migration** (see [Current status](#current-status)): SQLite cannot be seeded, the mock templates are T-SQL, 35 backend tests fail, lint fails and CI is expected to fail.
- **Azure mode is less defended than SQLite mode:** no authorizer, `query_only` or query timeout in the executor (`pyodbc`'s `timeout` is the connection timeout), and the schema is loaded once at first use and never refreshed.
- **Docker images have not been built** (Docker was unavailable during development) and the Dockerfile's ODBC step will fail as written. CI has not run on GitHub.
- **Credentials were committed** in an earlier revision; treat them as compromised and rotate them.
- **Admin auth is a single shared key**, and `/query` is public. Use per-user login or SSO and a trusted-proxy setting for the rate limiter before exposing this beyond a demo.
- **With a hosted LLM provider, question context and up to 20 result rows (for summaries) leave your machine.** Turn off LLM summaries or use Ollama if that matters.
- **Heuristics:** the classifier and ambiguity detection are keyword and synonym based; non-English or unusually phrased requests may be misjudged. The validator and executor are the real protection.
- **Costs:** a question can trigger up to four LLM calls (generate, retry, self-heal, summarise); there is no caching or token budget across requests.
- **Frontend:** no browser-level tests; the sort control is not keyboard accessible.
- **Small sample:** the evaluation set has 25 questions.

## Troubleshooting

- `npm` blocked by PowerShell execution policy: use `npm.cmd`.
- Admin pages say the key was rejected: the backend `ADMIN_API_KEY` must match what you type; a 503 means the server has none configured.
- First query is slow (a few seconds): the embedding model loads on first use; it is cached afterwards.
- `Analytics database not found` in SQLite mode: `data/seed_db.py` currently fails against the T-SQL `schema.sql`; restore a SQLite-compatible schema or switch to `DB_TYPE=azuresql`.
- `ModuleNotFoundError: pyodbc`: run `pip install -e ".[dev]"` again; `pyodbc` is now a core dependency. On Linux also install `unixodbc`.
- Azure queries fail with `Data source name not found` or a driver error: install ODBC Driver 18 and check the `Driver={...}` name in the connection string matches `pyodbc.drivers()`.
- The app still talks to SQLite after you set `DB_TYPE=azuresql`: the value must not have a comment glued to it (`DB_TYPE=azuresql# ...` is read as a different string).
