# Rork Backend Bridge

Paste a plain-English app description, get a deployable backend: Postgres schema, Alembic migrations, FastAPI routes, Supabase config, OpenAPI spec, and a prompt you paste back into Rork to wire your frontend to real endpoints.

---

## Architecture

```
Browser
  │  POST /api/generate (form data)
  ▼
Next.js 14 (App Router)
  │  proxy — streams SSE through
  ▼
FastAPI  ──────────────────────────────────────── Postgres
  │                                               (docker-compose)
  │  Step 1: parse description
  │  Step 4: scaffold routes       ── Claude API (claude-sonnet-4-5)
  │  Step 7: write Rork prompt ──/
  │
  └─ Steps 2,3,5,6: pure Python (schema SQL, Alembic, Supabase config, OpenAPI YAML)
  
SSE stream → browser renders live event timeline + tabbed artifact panel
```

---

## Quick start

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY in .env

docker compose up -d          # Postgres on :5432, FastAPI on :8000

cd frontend
npm install
npm run dev                   # Next.js on :3000
```

Open `http://localhost:3000`.

---

## How it works

Each form submission fires a single `POST /generate` that returns a `text/event-stream`. Seven steps run in sequence; each yields one or two SSE events the browser renders as they arrive.

1. **Parse** — Claude reads the description and returns a structured JSON schema: table names, column types, FK/M2M relationships. Max 8 tables; `users` is always injected with `id`, `email`, `password_hash`.
2. **Schema SQL** — Pure Python topological sort over the parsed tables → `CREATE TABLE` statements with `gen_random_uuid()` PKs, FK constraints, and indexes on every FK column.
3. **Migrations** — Pure Python Alembic migration (`upgrade` / `downgrade`) built from the same parsed schema, no second Claude call.
4. **FastAPI Routes** — Claude receives the schema SQL and returns one `APIRouter` per table with CRUD stubs, Pydantic request/response models, and SQLAlchemy session injection.
5. **Supabase Config** — Pure Python: `supabase/config.toml` + a TypeScript client snippet pre-populated with the generated table names.
6. **OpenAPI Spec** — Pure Python: OpenAPI 3.1 YAML assembled from the parsed table list and CRUD route shapes, rendered with PyYAML.
7. **Rork Prompt** — Claude receives the app description + route list and writes a 150–250 word prompt the developer pastes into Rork to replace mock data with real `fetch()` calls.

---

## Example

**Input**

> A habit tracking app where users set daily goals, log completions, build streaks, and see a weekly summary dashboard.

**Artifacts produced**

| Tab | Contents |
|-----|----------|
| Schema SQL | `habits`, `completions`, `streaks` tables + `users`, FK indexes |
| Migrations | Alembic file with `upgrade()` / `downgrade()` for all 4 tables |
| FastAPI Routes | `habits_router`, `completions_router`, `streaks_router` with typed stubs |
| Supabase Config | `config.toml` + `createClient` snippet referencing all tables |
| OpenAPI Spec | 3.1 YAML with list/create/get/update/delete paths per table |
| Back to Rork | Prompt referencing `/habits`, `/completions/{id}`, `/streaks` by path |

---

## Tech stack

| Tool | Why |
|------|-----|
| **Next.js 14 App Router** | Route Handlers stream `ReadableStream` directly — zero extra SSE libraries |
| **FastAPI** | `StreamingResponse` with an async generator maps cleanly to the step-by-step pipeline |
| **Anthropic Python SDK** | `AsyncAnthropic` keeps the SSE stream non-blocking while Claude calls run |
| **Pydantic v2** | `model_validate_json` gives a single-line parse + validation of Claude's JSON output |
| **SQLAlchemy / Alembic** | Industry-standard ORM and migration tool — generated code drops straight into any Python backend |
| **PyYAML** | Turns a plain Python dict into valid OpenAPI 3.1 YAML without a schema-specific library |
| **Postgres 15** | `gen_random_uuid()` via `pgcrypto`, native `UUID` type, `TIMESTAMPTZ` — no application-level UUID generation needed |
| **Docker Compose** | Single `docker compose up` gets Postgres + FastAPI running with correct dependency ordering |
