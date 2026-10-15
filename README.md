# QueryMind AI

Ask questions about a business database in plain English. QueryMind writes T-SQL, checks that it is safe, runs it on a read-only Azure SQL connection and answers with a table, a chart and a short summary. It includes an admin console for curating schema descriptions, few-shot examples, guardrails and model settings.

- **Backend:** FastAPI, sqlglot (T-SQL), ChromaDB, `pyodbc` to **Azure SQL**. **Frontend:** Next.js.
- **LLM providers:** OpenAI, Anthropic, Ollama, or an offline rule-based `mock` provider (the default).
- **Tests:** more than 500 backend tests and 17 frontend tests pass (`python -m pytest -q`; one live-OpenAI test is skipped unless a key is set). They run against an in-memory test double of the database, not a real server.
- **Honest status:** the pipeline, safety layers, admin console and evaluation harness are implemented and tested. **Nothing has been run against a live Azure SQL server by the author of this repository, the LLM paths have only been exercised with fake clients, and the Docker images have never been built.** `data/check_azure.py` exists so you can verify your own server in one command. See [Known limitations](#known-limitations) before relying on any result.

## Contents

[Domain and data](#domain-and-data) | [How it works](#how-it-works) | [Quick start](#quick-start) | [Configuration](#configuration) | [Using it](#using-it) | [Safety model](#safety-model) | [Evaluation](#evaluation) | [Development](#development) | [Project layout](#project-layout) | [Assignment coverage](#assignment-coverage) | [Known limitations](#known-limitations)

## Domain and data

**Why e-commerce:** everyone understands it, and it naturally contains every pattern the assignment asks for: many-to-many joins (orders and products through order items), aggregates (revenue by category, orders by status), several date fields for temporal questions, and a real ambiguity ("revenue" can mean the order total or the money actually captured).

| Table | Rows | Table | Rows |
|---|---|---|---|
| customers | 1,000 | order_items | 15,000 |
| products | 200 | payments | 5,000 |
| orders | 5,000 | reviews | 3,000 |

The data is synthetic and deterministic. `backend/demo_data.py` is the single definition of the six tables and the generated rows; `data/schema.sql` (CREATE statements only) and `data/seed_azure.py` are derived from it, and the in-memory test double loads the same rows. The schema, relationships, value lists and the revenue ambiguity are documented in the [data dictionary](docs/DATA_DICTIONARY.md) (regenerate with `python data/generate_data_dictionary.py`).

## How it works

```
question -> Classifier -> Schema Linker -> Example Retriever -> SQL Generator -> SQL Validator -> SQL Executor -> Result Formatter
                                                                                      |  one retry with the error fed back
                                                                                      +--> (executor error: one self-healing retry)
```

| Stage | What it does | Code |
|---|---|---|
| Classifier | Detects destructive requests and labels the question simple, aggregate, join or temporal; reports tables and columns mentioned | `backend/components/query_classifier.py` |
| Schema Linker | Reads the live schema from Azure's catalog (cached, refreshed every `SCHEMA_CACHE_TTL_S` seconds), merges admin descriptions and synonyms, finds values such as city names by scanning low-cardinality columns, flags ambiguous terms, builds the schema context (join paths, foreign keys) for the model and excludes sensitive columns | `schema_linker.py`, `backend/schema_index.py` |
| Example Retriever | Embeds the question with a local MiniLM model and returns the 3 most similar curated examples from ChromaDB; the index re-syncs when examples change | `example_retriever.py` |
| SQL Generator | `mock`: rule-based T-SQL templates. Real providers: a prompt with the T-SQL rules, schema context, detected values, ambiguities and examples; expects JSON back and can refuse with an explanation | `sql_generator.py`, `llm_generator.py`, `backend/llm.py` |
| SQL Validator | Parses T-SQL with sqlglot, enforces the safety rules below, adds `TOP n` | `sql_validator.py`, `backend/tsql_rules.py` |
| SQL Executor | Accepts exactly one plain query, applies the row cap and query timeout through the database interface; every call ends in a rollback | `sql_executor.py`, `backend/db.py` |
| Result Formatter | Table plus a 1 to 2 sentence summary (LLM, falling back to a deterministic summary) | `result_formatter.py` |

Every stage exchanges typed Pydantic models (`backend/schemas.py`), is independently callable, and any failure returns a clear status (`blocked`, `error` or `needs_clarification`) instead of a fallback query. `backend/pipeline.py` wires the stages together and `backend/factory.py` assembles them.

Two databases are used: the **analytics database** (Azure SQL, read-only for the app) behind the `AnalyticsDatabase` interface in `backend/db.py`, and an **app database** (`data/app.db`, always local SQLite) for examples, column metadata, query logs and settings, with versioned migrations.

## Quick start

### Offline demo, no Azure needed

`ANALYTICS_BACKEND=memory` swaps Azure for an in-memory **test double** loaded with the demo dataset. It exists for tests, CI and demos, logs a warning on start and is not a substitute for SQL Server.

```bash
cp .env.example .env      # set ADMIN_API_KEY to a real secret and ANALYTICS_BACKEND=memory
docker compose up --build # never built by the author; CI builds it on first push
```

Frontend: http://localhost:3000. API docs: http://localhost:8000/docs. App data persists in the `querymind-data` volume. Without Docker, use the local steps below.

### Azure SQL

1. **Driver.** Install the [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) (the Docker image installs it for you).
2. **Read-only login.** The app must connect with a login that cannot write, for example (run as an administrator in the target database):

   ```sql
   CREATE USER querymind_ro WITH PASSWORD = '<a strong password>';
   ALTER ROLE db_datareader ADD MEMBER querymind_ro;
   ```
3. **Configure `.env`** (never commit it). Keep it on one line, quoted, with comments on their own lines:

   ```
   ANALYTICS_BACKEND=azure
   AZURE_SQL_CONNECTION_STRING="Driver={ODBC Driver 18 for SQL Server};Server=tcp:YOUR_SERVER.database.windows.net,1433;Database=YOUR_DATABASE;Uid=querymind_ro;Pwd=YOUR_PASSWORD;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
   ```
4. **Data.** For a brand-new database, put an admin login in `AZURE_SQL_ADMIN_CONNECTION_STRING` and run `python data/seed_azure.py --yes`. It is a dry run without `--yes`, and it **refuses to touch a database where any demo table already exists** (`--recreate` drops them; use it only on a throwaway database). If you already loaded the demo tables yourself, skip this step.
5. **Verify.** `python data/check_azure.py` is read-only. It checks the connection, that the login cannot write, the schema, row counts and category values, runs every shipped query (examples, ground truth, mock templates) on your server, and compares data-independent results with the test double. `FAIL` means something is wrong; `WARN` usually means your data differs from the demo data (`--strict` fails on warnings, `--json report.json` saves the result).

`/ready` reports `analytics_db` and `read_only_login`; it returns 503 if the server is unreachable or the login can write (`REQUIRE_READ_ONLY_LOGIN=false` turns the second check off).

### Local development

Requires Python 3.12+ and Node 20+.

```powershell
# Backend (Windows PowerShell; on macOS/Linux use: python3 -m venv .venv && source .venv/bin/activate)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env                  # set ADMIN_API_KEY, and ANALYTICS_BACKEND=memory for an offline run
uvicorn backend.main:app --reload --port 8000

# Frontend (second terminal; on Windows PowerShell use npm.cmd if script execution is disabled)
cd frontend
npm install
npm run dev                             # http://localhost:3000
```

`.vscode/launch.json` has matching debug configurations that load `.env`. The example retriever downloads an ~80 MB embedding model the first time it is used, even with the `mock` provider (cached in `~/.cache/chroma`). Set `EMBEDDING_MODE=hash` to skip it (lower retrieval quality, useful for tests and CI). On Linux, `pyodbc` needs the ODBC driver and `unixodbc` installed to reach Azure, but importing the backend does not require them.

## Configuration

Copy `.env.example` to `.env`. Everything has a default except `ADMIN_API_KEY` (and the connection string in Azure mode).

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_API_KEY` | none | Required for every `/admin/*` route; those routes return 503 if it is unset. The server warns if it is still `change-me` |
| `ANALYTICS_BACKEND` | `azure` | `azure` (the product) or `memory` (in-memory test double for tests, CI and offline demos) |
| `AZURE_SQL_CONNECTION_STRING` | empty | ODBC connection string for the app's **read-only** login. The container refuses to start without it in `azure` mode |
| `AZURE_SQL_ADMIN_CONNECTION_STRING` | empty | Admin login, used only by `data/seed_azure.py` |
| `SCHEMA_CACHE_TTL_S` | 300 | Seconds the discovered schema is cached (0 reloads on every question) |
| `REQUIRE_READ_ONLY_LOGIN` | true | `/ready` fails if the app's login has any write permission |
| `LLM_PROVIDER` / `LLM_MODEL` | `mock` / `gpt-4o-mini` | Initial provider and model (changeable later in the admin UI) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL` | empty | Provider credentials; keys never leave the server |
| `LLM_TIMEOUT_S` | 30 | Per LLM request |
| `APP_DB_PATH`, `CHROMA_PATH` | `data/...` | App database and vector index locations |
| `EMBEDDING_MODE` | `onnx` | `onnx` (local MiniLM) or `hash` (offline, for tests) |
| `QUERY_TIMEOUT_S`, `ABSOLUTE_MAX_ROWS` | 5, 10000 | Executor limits |
| `CORS_ORIGINS` | localhost:3000 | Allowed browser origins (no wildcard) |
| `RATE_LIMIT_PER_MIN` | 60 | Per client IP for `/query`; 0 disables |
| `LOG_FORMAT`, `LOG_LEVEL` | `text`, `INFO` | `json` for one structured object per line |
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
| Model Config | Provider, model, temperature, max tokens, LLM summaries on or off (the dialect is fixed to T-SQL) |

### API

Interactive docs at `/docs`. Every route except `/health` and `/ready` is also served under `/api/v1` (the frontend uses that prefix).

| Method and path | Auth | Purpose |
|---|---|---|
| `POST /query` | none (rate limited) | `{"nl_query": "...", "max_rows": 100, "clarify_ambiguities": false, "column_choices": {}}` |
| `GET /health`, `GET /ready` | none | Liveness; readiness with database, read-only-login and provider checks |
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
2. **Validator** (sqlglot T-SQL AST, not string matching): exactly one `SELECT` or `UNION` (CTEs allowed); no DML, DDL, `EXEC`, `MERGE`, variables, transactions or `SELECT ... INTO` anywhere in the tree (including inside CTEs); `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE` and server-information functions are blocked; only `dbo`-qualified or unqualified tables are accepted, so `sys.*`, `INFORMATION_SCHEMA`, cross-database names and temp tables are blocked; every table, column and alias must exist; internal tables are blocked; sensitive columns are blocked even through `SELECT *`; `TOP n` is added or lowered to the guardrail maximum (also for `UNION`, CTEs and `OFFSET ... FETCH`); comments are stripped. It also rejects SQLite habits (`LIMIT`, `strftime`, `date('now')`) and select aliases used in `WHERE`/`GROUP BY`/`HAVING` with a hint the model can act on. Toggling an operation off in the Guardrails panel lets it past this layer only.
3. **Executor:** accepts exactly one plain query (a second, independent parse), applies the row cap and a query timeout, and every call ends in a rollback, so even a write that slipped through could not commit.
4. **Database login:** the app's login should have no write permission, and `/ready` fails if it does. This is the final enforcement and it is configured on your server, not by this code.
5. **Prompt hygiene:** the question is wrapped in tags and treated as data; sensitive columns are never in the prompt.
6. **Service level:** admin routes need a key and fail closed, CORS is an allow-list, `/query` is rate limited, logs never contain keys, query strings, connection strings or question text, and unexpected errors return a generic body with a request ID.

## Evaluation

```bash
python eval/evaluation_harness.py                      # offline mock provider, in-memory test double
python eval/evaluation_harness.py --provider openai    # needs OPENAI_API_KEY
python eval/evaluation_harness.py --database azure     # your Azure SQL database
```

The harness runs the real pipeline in-process, never substitutes fallback SQL, and exits non-zero if a gate fails (execution accuracy below 50%, any guardrail miss, database contents changing, false positives). Reports are written to `eval/reports/` as Markdown and JSON with the commit hash, which database was used and a fingerprint of its contents. `--database azure` requires a read-only login, checks connectivity first, and skips the resource-bomb queries unless you pass `--azure-resource-attacks` (they deliberately use the full query timeout on your server).

Committed baseline, **`mock` provider on the in-memory test double** ([full report](eval/reports/eval_mock_20261015_200941.md)):

| Measure | Result |
|---|---|
| Execution accuracy, original 25 questions | 25/25 (95% CI 87-100%) |
| Exact match (sqlglot AST diff) | 25/25 |
| Execution accuracy, same questions reworded | **6/25 (24%)** |
| Guardrails (40 natural-language attacks and 116 attack SQL statements) | 156/156; the executor alone denies 74/74 destructive statements; database contents unchanged; 2/2 resource bombs contained |
| False positives | 0/12 benign questions, 0/67 legitimate SQL statements |

**Read the first rows with care.** The templates were written while looking at those 25 questions, so 100% says nothing about generalisation; the reworded set (24%) is the honest signal for `mock`. A meaningful accuracy number needs a run with a real LLM, which has not been done, and a result that reflects your server needs `--database azure`. With 25 questions the confidence intervals are wide.

## Development

```bash
python -m pytest -q          # backend tests (in-memory test double; no Azure, no network needed)
ruff check .                 # lint
cd frontend && npm test && npm run build
```

GitHub Actions (`.github/workflows/ci.yml`) runs lint, the backend tests, the evaluation gates, the frontend tests and build, and a Docker job that builds both images, checks the ODBC driver inside the image, checks the entrypoint refuses to start without a connection string, and smoke-tests the container against the test double. It has not run on GitHub yet. Dependabot proposes weekly dependency updates. `uv.lock` is not regenerated by this project's tooling; run `uv lock` if you use `uv sync --locked`.

## Project layout

```
backend/            FastAPI app, pipeline, typed schemas, components/, store, security, observability
  db.py             AnalyticsDatabase interface and the Azure SQL adapter (pyodbc, imported lazily)
  demo_data.py      the demo dataset: tables, deterministic rows, T-SQL DDL
  tsql_rules.py     T-SQL checks shared by the validator and the test double
  testing/          in-memory test double of the database (tests, CI, offline demos)
data/               schema.sql (generated), seed_azure.py, check_azure.py, seed JSON (column metadata, 42 examples), data dictionary generator
docs/               DATA_DICTIONARY.md
eval/               evaluation harness, metrics, test/paraphrase/guardrail sets, committed report
frontend/           Next.js chat UI and admin panels
tests/              backend tests
docker/, Dockerfile, docker-compose.yml, .github/, .vscode/launch.json
DEMO.md             step-by-step demo script and status table
```

## Assignment coverage

| Deliverable | Status |
|---|---|
| Text-to-SQL pipeline, all components, independently callable with typed schemas | Done |
| Database, seed script, data dictionary | Azure SQL, deterministic synthetic data, guarded seed script, generated data dictionary. Not exercised against a live server by the author |
| FastAPI backend with `/query` and the admin routes | Done |
| Next.js chat UI and five admin panels with persisted CRUD | Done; verified by build, unit tests and manual checks, no browser test suite |
| 30+ curated few-shot examples | 42 in T-SQL, all verified to validate and return rows on the test double, none overlapping the evaluation questions |
| Evaluation report on a 25-query set | Harness and mock baseline done; **no real-LLM run yet** and no run against a live server |
| README | This file |

Bonus items: ambiguity resolution (done), self-healing SQL (done), chart auto-generation (done), voice input (done), query explanation (partial: shows the model's explanation and what was resolved, not a generated plain-English breakdown of the SQL). Not done: semantic caching. The generator targets T-SQL only; dialect transpilation is used inside the test double, not as a feature.

## Known limitations

- **No real-LLM verification.** The OpenAI, Anthropic and Ollama code paths are tested against fakes only. Accuracy with a real model is unmeasured.
- **The mock provider memorised the test set** (100% on the original wording, 24% reworded). It exists for offline demos and tests.
- **Not verified against a live Azure SQL server.** Every T-SQL query, the catalog queries, the permission probe and the Azure adapter were verified only against the in-memory test double and stub connections. The double is an approximation: it emulates case-insensitive text comparison, integer `AVG` truncation and several rejected constructs, but anything else is only as faithful as sqlglot's translation. Run `python data/check_azure.py` against your server before trusting results.
- **Docker images have not been built** (Docker was unavailable during development). The Dockerfile follows Microsoft's Debian 12 instructions and CI builds it on first push.
- **Credentials were committed** in an earlier revision and remain in the git history on the remote; treat them as compromised and rotate them. A test now fails if a password-like value is committed again.
- **T-SQL on Azure SQL / SQL Server only.** PostgreSQL, MySQL and SQLite analytics databases are not supported.
- **The read-only guarantee ends at your database login.** The validator, executor guard and rollback are defence in depth, but only the login's permissions are enforced by the server. The query timeout is the driver's query timeout.
- **Schema changes** on the server are picked up within `SCHEMA_CACHE_TTL_S` seconds (default 300) or on restart; there is no refresh button yet.
- **Ties:** questions such as "top 3 most reviewed products" have tied counts in the demo data; the ground truth adds a tie-break, so a model that does not could be scored wrong on that question.
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
- `/ready` returns 503 with `analytics_db` false: the detail names the cause (not set, driver missing, cannot connect). Run `python data/check_azure.py`.
- `/ready` returns 503 with `read_only_login` false: the app's login can write (its detail lists which role or permission). Use a read-only login, or set `REQUIRE_READ_ONLY_LOGIN=false` knowingly.
- `Data source name not found` or a driver error: install ODBC Driver 18 and check the `Driver={...}` name in the connection string matches `pyodbc.drivers()`.
- The container exits at start with `AZURE_SQL_CONNECTION_STRING is not set`: put it in `.env`, or set `ANALYTICS_BACKEND=memory` for an offline demo.
- `ANALYTICS_BACKEND` is rejected as unknown: the value must not have a comment glued to it (`ANALYTICS_BACKEND=azure# ...` is read as a different string); keep comments on their own line.
- Queries that worked in an earlier SQLite version now say "T-SQL has no LIMIT" or "'strftime' is not a recognized built-in function": that is the validator giving the model (or you) the T-SQL equivalent; see the hint in the message.
