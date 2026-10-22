# QueryMind AI - Demo Script

Status: the app runs on Azure SQL (T-SQL). All outputs below were measured with the `mock` provider on the in-memory test double (`ANALYTICS_BACKEND=memory`) unless stated otherwise. Nothing has been run against a live Azure server by the author; section 6 shows how you verify yours. See README.md for setup, limitations and the assignment coverage table.

## 0. Before you demo

1. **Pick the database.** `ANALYTICS_BACKEND=azure` with your read-only `AZURE_SQL_CONNECTION_STRING`, or `ANALYTICS_BACKEND=memory` for an offline demo on the test double (it logs a warning, say so out loud).
2. **If you use Azure, verify it first:** `python data/check_azure.py`. Fix every FAIL; read the WARNs (usually "your data differs from the demo data"). `/ready` must say `ready`; it returns 503 if the login can write.
3. **Row counts and sample names below come from the demo dataset.** If your Azure data differs, expect different numbers. Date-based questions ("last 30 days") depend on when the data was loaded.
4. **Rotate the Azure password** that was committed in an earlier revision (it remains in the git history on GitHub).
5. Run `python -m pytest -q` and `ruff check .` so you know the state you are presenting.

## 1. What to say (30 seconds)

A business user types a question in English. QueryMind turns it into T-SQL, checks the SQL is safe, runs it on a read-only Azure SQL connection and returns a table. Safety is enforced in independent layers, so a destructive statement cannot reach the data.

## 2. Architecture

```
POST /api/v1/query { "nl_query": "...", "max_rows": 100 }
  1. Classifier    query type; blocks destructive intent      -> status "blocked"
  2. Schema Linker relevant tables (schema read from Azure's catalog, cached)
  3. Retriever     few-shot examples from app.db
  4. Generator     T-SQL (mock templates, or an LLM when a provider and key are configured)
  5. Validator     sqlglot T-SQL safety + schema checks, adds TOP n
                   invalid -> one retry with the error fed back, else "blocked"
  6. Executor      one plain query, row cap, query timeout, always rolled back
  7. Formatter     table + summary
  -> log row saved to app.db -> JSON response
```

- Components: `backend/components/`. Orchestrator: `backend/pipeline.py`. Typed contracts: `backend/schemas.py`. Database access: `backend/db.py`.
- Any stage failure returns a clear error. There is no fallback query.

| DB | Where | Access |
|---|---|---|
| Analytics (customers, orders, order_items, products, payments, reviews) | Azure SQL | read-only login, executor only |
| App data (examples, column metadata, logs, settings) | `data/app.db` (local SQLite) | read/write, admin side |

## 3. Setup

```powershell
pip install -e ".[dev]"
copy .env.example .env      # set ADMIN_API_KEY; ANALYTICS_BACKEND=memory for offline, or azure + connection string
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
```

Open http://localhost:8000/docs and use POST /api/v1/query. A new, empty Azure database is seeded with `python data/seed_azure.py --yes` (admin connection string; refuses to touch a database that already has demo tables).

## 4. Live scenarios (measured with the demo dataset)

| Question | Result |
|---|---|
| Count orders by status | success, 3 rows: cancelled 1670, completed 1686, pending 1644; SQL `SELECT TOP 100 status, COUNT(*) AS order_count ...` (the `TOP` is added by the validator) |
| Top 5 customers by total amount spent | two-column result from a JOIN of customers and orders, first row Neha Gupta 48732.43, SQL starts `SELECT TOP 5 ... ORDER BY spent DESC` |
| Total revenue per product category | aggregate over order_items, 5 categories (Apparel 4404303.92, Books 5358449.75, Electronics 3240528.46, ...) |
| Orders in last 30 days | `DATEADD(DAY, -30, CAST(GETDATE() AS DATE))`; row count depends on today's date |
| Drop the customers table | status `blocked`, stage `classifier`, no SQL generated |
| Show updated orders | passes the classifier (only real destructive commands are blocked); the mock has no template for it, so status `error` at the generator |
| what is the meaning of life | status `error`, stage `generator`, no fallback query |

## 4b. Schema linking, ambiguity and sensitive columns

| Action | Expected result |
|---|---|
| Ask "Show all customers from Mumbai" and open `resolved_entities` | tables `customers`, column `customers.city`, value hit `customers.city = 'Mumbai'` (found by scanning low-cardinality column values, no hard-coded cities); 76 rows in the demo data |
| Ask "show revenue by month" | `ambiguities`: revenue could be `orders.total_amount` or `payments.amount` |
| Same question with `"clarify_ambiguities": true` | status `needs_clarification`, message "'revenue' is ambiguous. Did you mean `orders.total_amount` or `payments.amount`?" |
| Same question plus `"column_choices": {"revenue": "orders.total_amount"}` | ambiguity resolved, query runs (13 months of data in the demo set) |
| Ask "orders revenue" or "revenue from payments" | resolved automatically from the table you mentioned |
| Mark `customers.email` sensitive (Schema Manager), then ask for customer email | status `blocked` at the validator: "Sensitive column(s) not allowed"; the column is also removed from `schema_context` (what the LLM sees) |

`schema_context` lists the relevant tables with column descriptions, synonyms and foreign keys, plus the join-path tables (customers + products pulls in orders and order_items). It is built from Azure's catalog, so renamed or added columns appear within `SCHEMA_CACHE_TTL_S` seconds.

## 4c. Example retrieval

| Action | Expected result |
|---|---|
| Ask "How much did each payment method bring in?" and open `few_shot_examples` | top result is "How much money was collected by each payment method?" with a similarity score |
| Ask "which clients live in Delhi" | top result is "List all customers from Delhi" (semantic match: clients = customers) |
| Add, edit or delete an example in the Examples Manager and ask again | the vector index re-syncs on the next query; only changed examples are re-embedded |

42 curated T-SQL examples (8 simple, 14 aggregate, 8 join, 12 temporal), all validated and executed on the test double and returning rows. None overlaps with the 25 held-out evaluation questions, enforced by a test. Embeddings come from ChromaDB's MiniLM model (ONNX, local, no API key); if it is unavailable the retriever falls back to keyword ranking.

## 4d. LLM generation, self-healing and summaries

Switch provider by setting `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in `.env` (or in the Model Config panel). Providers: `mock` (offline templates, default), `openai`, `ollama` (local, OpenAI-compatible) and `anthropic`. These code paths are only tested with fake clients; no real model has been run.

| Action | Expected result |
|---|---|
| Ask a phrasing the templates never saw, e.g. "Which city has the most customers?" with provider `openai` | the LLM writes T-SQL from the schema context, descriptions, detected values and the 3 retrieved examples; the validator still checks it before execution |
| Look at `explanation` in the response | one sentence from the model, including which column it chose for an ambiguous term |
| Ask something unanswerable | the model returns `sql: null`; status `error` at the generator with its explanation, nothing executes |
| The model writes SQLite-style SQL (`LIMIT`, `strftime`) or an alias in `GROUP BY` | the validator rejects it with a T-SQL hint; the error and the previous SQL go back for one corrected attempt; `self_healed: true` when the second attempt succeeds |
| Read `result.nl_summary` | 1-2 sentence plain-English answer from the first 20 rows; falls back to a deterministic summary if the LLM fails or is switched off |
| Make the model misbehave (tests simulate an injected `DELETE FROM orders`) | blocked at the validator, retried once, never executed |

The prompt teaches the T-SQL rules the validator enforces: `TOP` not `LIMIT`, `DATEADD`/`CAST(GETDATE() AS DATE)` for dates, `FORMAT(col, 'yyyy-MM')`, **`AVG(CAST(col AS FLOAT))` for integer columns because SQL Server truncates integer averages**, no select aliases in `WHERE`/`GROUP BY`/`HAVING`, and an alias on every computed column. The question is wrapped in `<question>` tags and treated as data; API keys are never logged.

## 4e. Admin API

Set `ADMIN_API_KEY` in `.env`, then send it as `X-API-Key` (or `Authorization: Bearer`). Without the key every `/admin/*` route returns 401; if the server has no key configured they return 503 (fail closed).

```powershell
$h = @{ "X-API-Key" = "change-me" }
Invoke-RestMethod http://localhost:8000/api/v1/admin/guardrails -Headers $h
```

| Panel | Endpoints | Demo |
|---|---|---|
| Schema Manager | `GET /admin/schema`, `PUT /admin/schema/{table}/{column}` | mark `customers.email` sensitive, ask for customer email: blocked; un-mark: works |
| Examples Manager | `GET/POST /admin/examples`, `PUT/DELETE /admin/examples/{id}`, `GET /admin/examples/search?q=` | add an example, search for it semantically, ask a similar question and see it retrieved; a bad example (`DROP TABLE`, `LIMIT`, unknown column) is rejected with the validator's errors |
| Query Logs | `GET /admin/logs` (filters, `X-Total-Count`), `GET /admin/logs/export` (CSV, formula-injection safe), `GET /admin/stats` | run a few queries, filter by status, export |
| Guardrails | `GET/PUT /admin/guardrails` | set `max_rows` to 5: the very next query returns `SELECT TOP 5 ...`; set `allowed_table_prefixes` to `["order"]`: customer questions are blocked |
| Model Config | `GET/PUT /admin/config` | switch provider, temperature, max tokens; selecting a provider whose key is not on the server is rejected (422); the dialect is fixed to `tsql`. Keys are never returned |

Other hardening: CORS limited to `CORS_ORIGINS` (no wildcard, no credentials), `/query` rate limit per client IP (`RATE_LIMIT_PER_MIN`, 429 with `Retry-After`), internal tables cannot be unblocked, admin key comparison is constant-time. `/ready` shows `analytics_db` and `read_only_login`.

## 4f. Frontend

Run the backend (`uvicorn backend.main:app --port 8000`) and the frontend (`cd frontend; npm install; npm run dev`; use `npm.cmd` if PowerShell blocks scripts), then open http://localhost:3000.

| Screen | What to show |
|---|---|
| Chat | suggestion chips; summary, sortable and paged table, auto bar/line chart for 2-column numeric results, collapsible SQL with copy; "Explain" toggle shows the model's explanation, tables, columns and detected filters; Mic button (browsers with Web Speech API) |
| Chat, clarification | ask "Show revenue by month" with "Ask me when unsure" on: buttons for `orders.total_amount` / `payments.amount`; click one and the query runs with that choice |
| Chat, safety | "Drop the customers table" shows a blocked message with the stage that stopped it |
| /admin/* | enter the admin key once per tab; wrong key is rejected; Sign out clears it; a 401 later sends you back to the gate |
| Schema Manager | pick a table, edit descriptions and synonyms, tick Sensitive, Save per row; then ask for that column in Chat |
| Examples Manager | add, edit and delete examples (bad SQL is rejected with the validator errors shown), filter by type or text, semantic search with similarity scores |
| Query Logs | stats cards, filter by status or text, pagination, Export CSV |
| Guardrails | tick which operations are blocked, set max rows, table prefixes; applies to the next query |
| Model Config | provider, model, temperature, max tokens, LLM summary toggle; the dialect shows "T-SQL (Azure SQL)" and cannot be changed |

The admin key is typed into the page and kept in `sessionStorage` for the tab only; it is not bundled into the JavaScript. The UI has not been clicked through by an automated browser test.

## 4g. Evaluation

```powershell
.\.venv\Scripts\python.exe eval/evaluation_harness.py                         # mock provider, in-memory test double
.\.venv\Scripts\python.exe eval/evaluation_harness.py --provider openai       # needs OPENAI_API_KEY
.\.venv\Scripts\python.exe eval/evaluation_harness.py --database azure        # your server; read-only login required
```

Writes `eval/reports/eval_<provider>_<timestamp>.md` and `.json` (commit hash, which database, a fingerprint of its contents, per-query results). Exit code is non-zero if any gate fails: execution accuracy >= 50%, 100% guardrail compliance, the executor denies every destructive statement, resource attacks contained, database contents unchanged, no false positives. Against Azure the resource-bomb queries are skipped unless you pass `--azure-resource-attacks`.

Committed baseline (mock provider, test double, `eval/reports/eval_mock_20261015_200941.md`): 25/25 on the original wording but only 6/25 (24%) on paraphrases. That gap is the point: the template generator memorised the test set, so only a real LLM run gives a trustworthy accuracy number. Guardrails: 156/156 (40 natural-language attacks and 116 attack statements, including T-SQL vectors such as `EXEC`, `SELECT INTO`, `OPENROWSET`, `sys.*`, cross-database names, variables and `WAITFOR`); the executor alone denies 74/74 destructive statements; contents unchanged. Never quote the old "74.8%" figure; quote the committed report with its caveats.

## 4h. Run it with Docker

```powershell
copy .env.example .env        # set ADMIN_API_KEY, and the Azure connection string (or ANALYTICS_BACKEND=memory for an offline demo)
docker compose up --build     # first build installs the ODBC driver and downloads the embedding model into the image (about 80 MB)
```

Open http://localhost:3000 (chat and admin) and http://localhost:8000/docs (API). `docker compose` refuses to start without `ADMIN_API_KEY`, and the backend container refuses to start in `azure` mode without `AZURE_SQL_CONNECTION_STRING`. App data (app database, vector index) lives in the `querymind-data` volume. `NEXT_PUBLIC_API_URL` is compiled into the frontend; rebuild after changing it.

**The images have never been built by the author** (no Docker on the development machine). The CI `docker` job builds them, checks the ODBC driver inside the image and smoke-tests a query on the first push. If you are demoing this live, build it beforehand.

## 5. Safety demo (validator and executor)

Run in a Python shell with `ANALYTICS_BACKEND=memory`: `from main import validator`, then `validator.validate(sql)`. These results were measured:

| SQL | Result |
|---|---|
| `SELECT 1; DROP TABLE orders` | rejected: Exactly one SQL statement is allowed |
| `SELECT * FROM query_logs` | rejected: Table 'query_logs' is not accessible |
| `SELECT * FROM orders` | allowed, rewritten to `SELECT TOP 100 * FROM orders` |
| `SELECT * FROM orders LIMIT 5` | rejected with a hint: T-SQL has no LIMIT; use SELECT TOP n |
| `SELECT * FROM orders WHERE order_date >= date('now')` | rejected with a hint: 'date' is not a recognized built-in function; use CAST/DATEADD/GETDATE |
| `SELECT FORMAT(order_date, 'yyyy-MM') AS m, COUNT(*) AS n FROM orders GROUP BY m` | rejected: a select alias cannot be used in GROUP BY; repeat the expression |
| `SELECT * INTO copy FROM orders` | rejected: SELECT INTO is not allowed (it parses as a SELECT but creates a table) |
| `EXEC sp_who` | rejected: Only SELECT statements are allowed |
| `SELECT * FROM sys.tables` | rejected: Schema-qualified table 'sys.tables' is not allowed |
| `WITH d AS (SELECT * FROM orders) DELETE FROM d` | rejected: DELETE statements are blocked by guardrails |

Defence in depth:
1. Classifier blocks destructive intent in the question.
2. Validator (sqlglot T-SQL AST): single SELECT/UNION, no DML/DDL/EXEC/INTO/variables anywhere in the tree, no OPENROWSET/OPENQUERY, `dbo` or unqualified tables only, known tables and columns, internal tables blocked, `TOP` enforced.
3. Executor: a second, independent check that the statement is one plain query, row cap, query timeout, and a rollback after every call.
4. The database login: it must be read-only, `/ready` fails if it is not, and this is the only layer the server itself enforces. Say plainly that this layer lives in your Azure configuration.

## 6. Verify your own server

```powershell
.\.venv\Scripts\python.exe data/check_azure.py
```

Read-only. It checks the connection, that the login cannot write, the schema, row counts and category values, then runs all 67 shipped queries (42 examples, 25 ground truth, mock templates) on your server and compares results with the test double: 38 queries by value, 10 by shape, 19 only that they run (they depend on today's date). `FAIL` is a real problem (a query your server rejects, a missing column, a writable login); `WARN` usually means your data differs from the demo data.

## 7. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

More than 500 backend tests, all against the in-memory test double and stub connections (no Azure, no network): validator (T-SQL rules and attacks), executor, schema index, pipeline (fail-closed, clarification), store, API and admin routes, LLM paths with fakes, the Azure adapter with stub connections, the seed and check scripts, the evaluation harness and its own meta-tests, README and deployment checks. Frontend: `cd frontend; npm test` runs 17 tests.

## 8. Not done yet (be upfront)

| Area | Current state |
|---|---|
| Live Azure verification | Nothing has been run against a real server by the author. Use `data/check_azure.py` and `eval --database azure` on yours; the permission probe and catalog queries are first exercised there |
| Live LLM verification | OpenAI/Anthropic/Ollama code paths are tested with fakes only; a real key has not been exercised |
| Evaluation with a real LLM | Harness is ready; only the mock baseline has been run (it memorised the test set, 24% on paraphrases). Run `--provider openai` once a key is available |
| Docker images | Dockerfile and compose are written and statically tested; `docker build` has never been run. CI builds them on first push |
| CI | Every step was run locally except the new Docker job; it has not run on GitHub yet |
| Frontend automation | Build and unit tests (17) cover the API client and data helpers; clicks, forms and chart rendering were checked by hand only |
| Admin auth | Single shared API key; fine for a demo, replace with per-user login/SSO for production |
| Committed credentials | An Azure login was committed in an earlier revision and remains in the git history; rotate the password |
| Semantic caching | Not implemented |

## 9. Final demo plan

1. Run `data/check_azure.py` (or state you are on the test double) and show `/ready`.
2. Chat UI: ask the scenarios above live; show the generated T-SQL, table, summary and the ambiguity prompt.
3. Admin panels: edit a column description, add an example, toggle a guardrail and show the effect on the next query.
4. Safety demo: the validator table above, then the adversarial set in the evaluation report.
5. Show the evaluation report with the per-query-type breakdown and say what the 24% paraphrase score means.
6. Run `pytest` and show CI (after the first push, including the Docker job).
