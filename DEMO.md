# QueryMind AI - Demo Script

Status: through Step 6 of 11. Update the "Not done yet" table and re-run every scenario at the end.

## 1. What to say (30 seconds)
A business user types a question in English. QueryMind turns it into SQL, checks the SQL is safe, runs it on a read-only database and returns a table. Safety is enforced in three independent layers, so a destructive statement cannot reach the data.

## 2. Architecture

```
POST /api/v1/query { "nl_query": "...", "max_rows": 100 }
  1. Classifier    query type; blocks destructive intent      -> status "blocked"
  2. Schema Linker relevant tables
  3. Retriever     few-shot examples from app.db
  4. Generator     SQL (template-based for now)
  5. Validator     sqlglot safety + schema checks, adds LIMIT
                   invalid -> one retry with the error fed back, else "blocked"
  6. Executor      read-only SQLite, timeout, row cap
  7. Formatter     table + summary
  -> log row saved to app.db -> JSON response
```

- Components: `backend/components/`. Orchestrator: `backend/pipeline.py`. Typed contracts: `backend/schemas.py`.
- Any stage failure returns a clear error. There is no fallback query.

| DB | File | Access |
|---|---|---|
| Analytics (customers, orders, order_items, products, payments, reviews) | `data/querymind.db` | read-only, executor only |
| App data (examples, column metadata, logs, settings) | `data/app.db` | read/write, admin side |

## 3. Setup

```powershell
.\.venv\Scripts\python.exe data/seed_db.py
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
```

Open http://localhost:8000/docs and use POST /api/v1/query.

## 4. Live scenarios (outputs recorded 2026-10-13)

| Question | Expected result |
|---|---|
| Count orders by status | success, 3 rows (cancelled 1670, ...), `LIMIT 100` added |
| Top 5 customers by total amount spent | 3-table JOIN, first row Neha Gupta 48732.43 |
| Total revenue per category | aggregate over order_items, 5 categories |
| Orders in last 30 days | date arithmetic, `DATE('now','-30 days')` |
| Drop the customers table | status `blocked`, stage `classifier`, no SQL generated |
| Show updated orders | allowed (word-aware classifier; only real destructive commands are blocked) |
| what is the meaning of life | status `error`, stage `generator`, no fallback query |

## 4b. Schema linking, ambiguity and sensitive columns (Step 5)

| Action | Expected result |
|---|---|
| Ask "Show all customers from Mumbai" and open `resolved_entities` | tables `customers`, column `customers.city`, value hit `customers.city = 'Mumbai'` (found by scanning low-cardinality column values, no hard-coded cities) |
| Ask "show revenue by month" | `ambiguities`: revenue could be `orders.total_amount` or `payments.amount` |
| Same question with `"clarify_ambiguities": true` | status `needs_clarification`, message "Did you mean `orders.total_amount` or `payments.amount`?" |
| Same question plus `"column_choices": {"revenue": "orders.total_amount"}` | ambiguity resolved, query runs |
| Ask "orders revenue" or "revenue from payments" | resolved automatically from the table you mentioned |
| Mark `customers.email` sensitive (`store.upsert_column_metadata`, admin UI in Step 9), then ask for customer email | status `blocked` at the validator: "Sensitive column(s) not allowed"; the column is also removed from `schema_context` (what the LLM sees) |

`schema_context` returned by the linker lists the relevant tables with column descriptions, synonyms and foreign keys, plus the join-path tables (e.g. customers + products pulls in orders and order_items). Step 7 feeds it to the LLM.

## 4c. Example retrieval (Step 6)

| Action | Expected result |
|---|---|
| Ask "How much did each payment method bring in?" and open `few_shot_examples` | top result is "How much money was collected by each payment method?" with a similarity score |
| Ask "which clients live in Delhi" | top result is "List all customers from Delhi" (semantic match: clients = customers) |
| Add/edit/delete an example (store API now, admin UI in Step 9) and ask again | the vector index re-syncs on the next query; only changed examples are re-embedded |

42 curated examples (8 simple, 14 aggregate, 8 join, 12 temporal), all verified to validate and return rows on the real data. None overlaps with the 25 held-out evaluation questions, enforced by a test. Embeddings come from ChromaDB's MiniLM model (ONNX, runs locally, no API key). If the model is unavailable the retriever falls back to keyword ranking.

## 5. Safety demo (validator and executor)

Run in a Python shell (`from backend.main import build_pipeline; v = build_pipeline().validator`):

| SQL | Result |
|---|---|
| `SELECT 1; DROP TABLE orders` | rejected: Exactly one SQL statement is allowed |
| `SELECT * FROM query_logs` | rejected: Table 'query_logs' is not accessible |
| `SELECT * FROM orders LIMIT 99999` | allowed, rewritten to `LIMIT 100` |
| `PRAGMA writable_schema=1` | rejected: Only SELECT statements are allowed |
| `WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d` | rejected: DELETE blocked |

Defence in depth:
1. Classifier blocks destructive intent in the question.
2. Validator (sqlglot AST): single SELECT/UNION, no DML/DDL/PRAGMA/ATTACH or dangerous functions anywhere, known tables and columns only, internal tables blocked, LIMIT enforced.
3. Executor: read-only connection, `query_only`, SQLite authorizer denying non-SELECT actions, 5 s timeout, row cap.

## 6. Admin data

After running the scenarios, open `/api/v1/admin/logs`: every query is logged with status, latency and failed stage.

Also available (read-only for now): `/admin/schema`, `/admin/examples`, `/admin/guardrails`, `/admin/config`.

## 7. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

118 tests across validator, components (classifier, linker, executor), schema index, pipeline (fail-closed and clarification), store and API.

## 8. Not done yet (be upfront)

| Area | Current state | Step |
|---|---|---|
| SQL generation | Templates, not an LLM; unseen phrasing returns a clean error | 7 |
| Admin writes | No add/edit/delete examples or guardrail toggles via API | 8 |
| Frontend | Chat page works; 5 admin panels only dump JSON; no clarification prompt yet | 9 |
| Evaluation | Old harness numbers are not reliable; held-out evaluation needed | 10 |
| Auth, CORS, Docker, CI | Not done | 8, 11 |

Do not demo the admin panels or quote the old "74.8%" figure until Steps 9 and 10 are finished.

## 9. Final demo plan
1. Chat UI: ask the scenarios above live.
2. Show the generated SQL, table, summary and ambiguity prompt.
3. Admin panels: edit a column description, add an example, toggle a guardrail and show the effect on the next query.
4. Guardrail demo with the adversarial set.
5. Show the evaluation report with the per-query-type breakdown.
6. Run `pytest` and show CI.
