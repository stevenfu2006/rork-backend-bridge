import json
import re
import uuid
from typing import AsyncGenerator, Optional

import anthropic
import yaml
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Request model ───────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    description: str
    app_name: str
    include_auth: bool = False
    include_supabase: bool = False

# ── Schema models (parsed from Claude) ─────────────────────────────────────────

class ColumnDef(BaseModel):
    name: str
    pg_type: str
    nullable: bool = True
    default: Optional[str] = None

class RelationshipDef(BaseModel):
    type: str  # "fk" | "m2m"
    target: str
    via_column: str

class TableDef(BaseModel):
    name: str
    columns: list[ColumnDef] = []
    relationships: list[RelationshipDef] = []

class AppSchema(BaseModel):
    app_name: str
    tables: list[TableDef] = []

# ── SSE helper ──────────────────────────────────────────────────────────────────

def sse(type: str, message: str, payload: str = "", progress: int = 0) -> str:
    return (
        "data: "
        + json.dumps({"type": type, "message": message, "payload": payload, "progress": progress})
        + "\n\n"
    )

# ── Anthropic model ─────────────────────────────────────────────────────────────

ANTHROPIC_MODEL = "claude-sonnet-4-5-20250514"

# ── String helpers ──────────────────────────────────────────────────────────────

def singularize(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses") or name.endswith("xes"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name

def to_pascal(name: str) -> str:
    return "".join(word.title() for word in name.split("_"))

def pg_to_sa_type(pg_type: str) -> str:
    t = pg_type.upper().split("(")[0].strip()
    return {
        "TEXT": "sa.Text()",
        "VARCHAR": "sa.Text()",
        "INTEGER": "sa.Integer()",
        "INT": "sa.Integer()",
        "BIGINT": "sa.BigInteger()",
        "BOOLEAN": "sa.Boolean()",
        "BOOL": "sa.Boolean()",
        "UUID": "postgresql.UUID(as_uuid=True)",
        "TIMESTAMPTZ": "sa.TIMESTAMP(timezone=True)",
        "TIMESTAMP": "sa.TIMESTAMP(timezone=True)",
        "JSONB": "postgresql.JSONB()",
        "JSON": "postgresql.JSON()",
        "FLOAT": "sa.Float()",
        "NUMERIC": "sa.Numeric()",
        "DECIMAL": "sa.Numeric()",
        "DATE": "sa.Date()",
        "BYTEA": "sa.LargeBinary()",
    }.get(t, "sa.Text()")

def pg_to_json_type(pg_type: str) -> str:
    t = pg_type.upper().split("(")[0].strip()
    return {
        "TEXT": "string", "VARCHAR": "string",
        "INTEGER": "integer", "INT": "integer", "BIGINT": "integer",
        "BOOLEAN": "boolean", "BOOL": "boolean",
        "UUID": "string",
        "TIMESTAMPTZ": "string", "TIMESTAMP": "string", "DATE": "string",
        "JSONB": "object", "JSON": "object",
        "FLOAT": "number", "NUMERIC": "number", "DECIMAL": "number",
    }.get(t, "string")

# ── FK lookup ───────────────────────────────────────────────────────────────────

def fk_lookup(schema: AppSchema) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for table in schema.tables:
        for rel in table.relationships:
            if rel.type == "fk":
                result[(table.name, rel.via_column)] = rel.target
    return result

# ── Topological sort ────────────────────────────────────────────────────────────

def topo_sort(tables: list[TableDef]) -> list[TableDef]:
    table_map = {t.name: t for t in tables}
    deps: dict[str, set[str]] = {t.name: set() for t in tables}
    for table in tables:
        for rel in table.relationships:
            if rel.type == "fk" and rel.target in table_map:
                deps[table.name].add(rel.target)

    visited: set[str] = set()
    result: list[TableDef] = []

    def dfs(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for dep in deps.get(name, set()):
            if dep in table_map:
                dfs(dep)
        if name in table_map:
            result.append(table_map[name])

    for t in tables:
        dfs(t.name)
    return result

# ── Schema SQL ──────────────────────────────────────────────────────────────────

def build_schema_sql(schema: AppSchema) -> str:
    fk = fk_lookup(schema)
    sorted_tables = topo_sort(schema.tables)
    m2m_seen: set[str] = set()
    parts = ['CREATE EXTENSION IF NOT EXISTS "pgcrypto";\n']

    for table in sorted_tables:
        col_lines = [
            "  id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY",
        ]
        for col in table.columns:
            target = fk.get((table.name, col.name))
            null_clause = "" if col.nullable else " NOT NULL"
            def_clause = f" DEFAULT {col.default}" if col.default else ""
            ref_clause = f" REFERENCES {target}(id) ON DELETE CASCADE" if target else ""
            col_lines.append(f"  {col.name:<22} {col.pg_type}{null_clause}{def_clause}{ref_clause}")
        col_lines += [
            "  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()",
            "  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()",
        ]
        parts.append(f"CREATE TABLE {table.name} (\n" + ",\n".join(col_lines) + "\n);\n")
        for col in table.columns:
            if (table.name, col.name) in fk:
                parts.append(f"CREATE INDEX ON {table.name} ({col.name});\n")

    for table in schema.tables:
        for rel in table.relationships:
            if rel.type != "m2m" or rel.via_column in m2m_seen:
                continue
            m2m_seen.add(rel.via_column)
            s1, s2 = singularize(table.name), singularize(rel.target)
            parts.append(
                f"CREATE TABLE {rel.via_column} (\n"
                f"  {s1}_id UUID NOT NULL REFERENCES {table.name}(id) ON DELETE CASCADE,\n"
                f"  {s2}_id UUID NOT NULL REFERENCES {rel.target}(id) ON DELETE CASCADE,\n"
                f"  PRIMARY KEY ({s1}_id, {s2}_id)\n);\n"
                f"CREATE INDEX ON {rel.via_column} ({s1}_id);\n"
                f"CREATE INDEX ON {rel.via_column} ({s2}_id);\n"
            )

    return "\n".join(parts)

# ── Alembic migration ───────────────────────────────────────────────────────────

def build_migration(schema: AppSchema) -> str:
    fk = fk_lookup(schema)
    sorted_tables = topo_sort(schema.tables)
    rev_id = uuid.uuid4().hex[:12]
    m2m_seen: set[str] = set()
    upgrade_stmts: list[str] = []
    downgrade_stmts: list[str] = []

    for table in sorted_tables:
        col_args = [
            "        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,\n"
            "                  server_default=sa.text('gen_random_uuid()')),",
        ]
        for col in table.columns:
            target = fk.get((table.name, col.name))
            if target:
                col_args.append(
                    f"        sa.Column('{col.name}', postgresql.UUID(as_uuid=True),\n"
                    f"                  sa.ForeignKey('{target}.id', ondelete='CASCADE'),\n"
                    f"                  nullable={col.nullable}),"
                )
            else:
                sa_t = pg_to_sa_type(col.pg_type)
                srv = f", server_default=sa.text('{col.default}')" if col.default else ""
                col_args.append(
                    f"        sa.Column('{col.name}', {sa_t}, nullable={col.nullable}{srv}),"
                )
        col_args += [
            "        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,\n"
            "                  server_default=sa.text('now()')),",
            "        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False,\n"
            "                  server_default=sa.text('now()')),",
        ]
        upgrade_stmts.append(
            f"    op.create_table('{table.name}',\n" + "\n".join(col_args) + "\n    )"
        )
        for col in table.columns:
            if (table.name, col.name) in fk:
                upgrade_stmts.append(
                    f"    op.create_index(op.f('ix_{table.name}_{col.name}'),\n"
                    f"                    '{table.name}', ['{col.name}'])"
                )
        downgrade_stmts.insert(0, f"    op.drop_table('{table.name}')")

    for table in schema.tables:
        for rel in table.relationships:
            if rel.type != "m2m" or rel.via_column in m2m_seen:
                continue
            m2m_seen.add(rel.via_column)
            s1, s2 = singularize(table.name), singularize(rel.target)
            upgrade_stmts.append(
                f"    op.create_table('{rel.via_column}',\n"
                f"        sa.Column('{s1}_id', postgresql.UUID(as_uuid=True),\n"
                f"                  sa.ForeignKey('{table.name}.id', ondelete='CASCADE'), nullable=False),\n"
                f"        sa.Column('{s2}_id', postgresql.UUID(as_uuid=True),\n"
                f"                  sa.ForeignKey('{rel.target}.id', ondelete='CASCADE'), nullable=False),\n"
                f"        sa.PrimaryKeyConstraint('{s1}_id', '{s2}_id'),\n"
                f"    )"
            )
            downgrade_stmts.insert(0, f"    op.drop_table('{rel.via_column}')")

    return (
        f'"""\n{schema.app_name}: initial schema\n\nRevision ID: {rev_id}\n"""\n'
        "from alembic import op\nimport sqlalchemy as sa\n"
        "from sqlalchemy.dialects import postgresql\n\n"
        f"revision = '{rev_id}'\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n\n\n"
        "def upgrade() -> None:\n"
        + "\n".join(upgrade_stmts)
        + "\n\n\ndef downgrade() -> None:\n"
        + "\n".join(downgrade_stmts)
        + "\n"
    )

# ── Supabase config ─────────────────────────────────────────────────────────────

def build_supabase_config(schema: AppSchema, include_auth: bool) -> str:
    table_names = [t.name for t in schema.tables]
    auth_flag = "true" if include_auth else "false"

    config_toml = (
        "# supabase/config.toml\n"
        f'project_id = "<your-project-ref>"\n\n'
        "[api]\nenabled = true\nport = 54321\nschemas = [\"public\"]\n"
        "extra_search_path = [\"public\", \"extensions\"]\nmax_rows = 1000\n\n"
        "[db]\nport = 54322\nshadow_port = 54320\nmajor_version = 15\n\n"
        f"[auth]\nenabled = {auth_flag}\n"
        'site_url = "http://localhost:3000"\n'
        "additional_redirect_urls = []\njwt_expiry = 3600\nenable_signup = true\n"
    )

    js_snippet = (
        "// src/lib/supabase.ts\n"
        "import { createClient } from '@supabase/supabase-js'\n\n"
        "const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!\n"
        "const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!\n\n"
        "export const supabase = createClient(supabaseUrl, supabaseAnonKey)\n\n"
        f"// Tables: {', '.join(table_names)}\n"
    )
    if include_auth:
        js_snippet += (
            "\nexport const signUp = (email: string, password: string) =>\n"
            "  supabase.auth.signUp({ email, password })\n\n"
            "export const signIn = (email: string, password: string) =>\n"
            "  supabase.auth.signInWithPassword({ email, password })\n\n"
            "export const signOut = () => supabase.auth.signOut()\n\n"
            "export const getUser = () => supabase.auth.getUser()\n"
        )

    return config_toml + "\n---\n\n" + js_snippet

# ── OpenAPI spec ────────────────────────────────────────────────────────────────

def build_openapi_spec(schema: AppSchema) -> str:
    paths: dict = {}
    components: dict = {}

    for table in schema.tables:
        model = to_pascal(singularize(table.name))
        props: dict = {"id": {"type": "string", "format": "uuid"}}
        for col in table.columns:
            entry: dict = {"type": pg_to_json_type(col.pg_type)}
            upper = col.pg_type.upper()
            if "UUID" in upper:
                entry["format"] = "uuid"
            elif "TIMESTAMP" in upper or "DATE" in upper:
                entry["format"] = "date-time"
            if col.nullable:
                entry["nullable"] = True
            props[col.name] = entry
        props["created_at"] = {"type": "string", "format": "date-time"}
        props["updated_at"] = {"type": "string", "format": "date-time"}

        create_props = {k: v for k, v in props.items() if k not in ("id", "created_at", "updated_at")}
        components[model] = {"type": "object", "properties": props}
        components[f"{model}Create"] = {"type": "object", "properties": create_props}

        ref = f"#/components/schemas/{model}"
        create_ref = f"#/components/schemas/{model}Create"
        id_param = {
            "name": "id", "in": "path", "required": True,
            "schema": {"type": "string", "format": "uuid"},
        }
        sing = singularize(table.name)

        paths[f"/{table.name}"] = {
            "get": {
                "summary": f"List {table.name}", "operationId": f"list_{table.name}",
                "responses": {"200": {"description": "OK", "content": {
                    "application/json": {"schema": {"type": "array", "items": {"$ref": ref}}}
                }}},
            },
            "post": {
                "summary": f"Create {sing}", "operationId": f"create_{sing}",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": create_ref}}}},
                "responses": {"201": {"description": "Created", "content": {"application/json": {"schema": {"$ref": ref}}}}},
            },
        }
        paths[f"/{table.name}/{{id}}"] = {
            "get": {
                "summary": f"Get {sing}", "operationId": f"get_{sing}",
                "parameters": [id_param],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": ref}}}}, "404": {"description": "Not found"}},
            },
            "put": {
                "summary": f"Update {sing}", "operationId": f"update_{sing}",
                "parameters": [id_param],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": create_ref}}}},
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": ref}}}}, "404": {"description": "Not found"}},
            },
            "delete": {
                "summary": f"Delete {sing}", "operationId": f"delete_{sing}",
                "parameters": [id_param],
                "responses": {"204": {"description": "No content"}, "404": {"description": "Not found"}},
            },
        }

    spec = {
        "openapi": "3.1.0",
        "info": {"title": f"{schema.app_name} API", "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": components},
    }
    return yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)

# ── Route list (used in Rork prompt) ───────────────────────────────────────────

def build_route_list(schema: AppSchema) -> str:
    lines = []
    for t in schema.tables:
        lines += [
            f"GET    /{t.name}",
            f"POST   /{t.name}",
            f"GET    /{t.name}/{{id}}",
            f"PUT    /{t.name}/{{id}}",
            f"DELETE /{t.name}/{{id}}",
        ]
    return "\n".join(lines)

# ── Prompts ─────────────────────────────────────────────────────────────────────

_PARSE_SYSTEM = """\
You are a database architect. Analyze an app description and return ONLY a JSON object — \
no markdown, no explanation, no code fences.

Shape:
{
  "app_name": "string",
  "tables": [
    {
      "name": "string",
      "columns": [{"name":"string","pg_type":"string","nullable":bool,"default":"string|null"}],
      "relationships": [{"type":"fk|m2m","target":"string","via_column":"string"}]
    }
  ]
}

Rules:
- Always include a users table. Its columns array must contain only: \
email (TEXT, nullable false), password_hash (TEXT, nullable false). \
Do NOT put id, created_at, updated_at in any table's columns array — they are added automatically.
- FK columns: name = {singular_target}_id, pg_type = UUID, nullable = false. \
Add a matching relationship {type:"fk", target:"target_table", via_column:"{singular_target}_id"}.
- M2M: via_column is the junction table name (e.g. "post_tags").
- Max 8 tables. Output ONLY the raw JSON object.\
"""

_ROUTES_SYSTEM = """\
You are a FastAPI expert. Generate router skeleton code. \
Return ONLY valid Python — no markdown fences, no explanation.\
"""

_RORK_SYSTEM = """\
You write concise, actionable integration guides for developers using the Rork mobile app builder.\
"""

# ── Pipeline ────────────────────────────────────────────────────────────────────

async def generate_pipeline(req: GenerateRequest) -> AsyncGenerator[str, None]:
    client = anthropic.AsyncAnthropic()
    schema: Optional[AppSchema] = None
    sql: str = ""

    # Step 1 — Parse ────────────────────────────────────────────────────────────
    yield sse("step", "Analyzing app structure…", progress=8)
    try:
        msg = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2048,
            system=_PARSE_SYSTEM,
            messages=[{"role": "user", "content": f"App name: {req.app_name}\n\n{req.description}"}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        schema = AppSchema.model_validate_json(raw)
    except Exception as exc:
        yield sse("error", f"Parse failed: {exc}", progress=8)
        return

    # Step 2 — Schema SQL ───────────────────────────────────────────────────────
    yield sse("step", "Writing Postgres schema…", progress=18)
    try:
        sql = build_schema_sql(schema)
        yield sse("schema", "Postgres schema ready", payload=sql, progress=28)
    except Exception as exc:
        yield sse("error", f"Schema build failed: {exc}", progress=18)
        return

    # Step 3 — Migrations ───────────────────────────────────────────────────────
    yield sse("step", "Generating Alembic migration…", progress=36)
    try:
        migration = build_migration(schema)
        yield sse("migrations", "Alembic migration ready", payload=migration, progress=46)
    except Exception as exc:
        yield sse("error", f"Migration build failed: {exc}", progress=36)
        return

    # Step 4 — FastAPI Routes ───────────────────────────────────────────────────
    yield sse("step", "Scaffolding API routes…", progress=52)
    try:
        routes_prompt = (
            f"Generate FastAPI router skeletons for the following Postgres schema.\n\n"
            f"Schema SQL:\n{sql}\n\n"
            "Requirements:\n"
            "- One APIRouter per table, e.g. router = APIRouter(prefix='/users', tags=['users'])\n"
            "- Pydantic models: Create{Model}Request, {Model}Response (with all columns + id/timestamps)\n"
            "- Five endpoints per table: GET / (list), POST / (create), "
            "GET /{id}, PUT /{id}, DELETE /{id}\n"
            "- SQLAlchemy session dependency: db: Session = Depends(get_db)\n"
            "- Import: from app.database import get_db\n"
            "- Body: raise HTTPException(status_code=501, detail='TODO: implement') "
            "with a # TODO comment on each endpoint\n"
            "- All routers collected in a list called `all_routers` at the bottom of the file\n"
            "- Single Python file, no markdown"
        )
        msg = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=_ROUTES_SYSTEM,
            messages=[{"role": "user", "content": routes_prompt}],
        )
        routes_code = msg.content[0].text.strip()
        routes_code = re.sub(r"^```(?:python)?\s*", "", routes_code)
        routes_code = re.sub(r"\s*```$", "", routes_code)
        yield sse("fastapi_routes", "FastAPI routes scaffolded", payload=routes_code, progress=62)
    except Exception as exc:
        yield sse("error", f"Routes generation failed: {exc}", progress=52)
        return

    # Step 5 — Supabase Config ──────────────────────────────────────────────────
    yield sse("step", "Building Supabase config…", progress=68)
    try:
        supabase_cfg = build_supabase_config(schema, req.include_supabase or req.include_auth)
        yield sse("supabase", "Supabase config ready", payload=supabase_cfg, progress=74)
    except Exception as exc:
        yield sse("error", f"Supabase config failed: {exc}", progress=68)
        return

    # Step 6 — OpenAPI Spec ─────────────────────────────────────────────────────
    yield sse("step", "Assembling OpenAPI spec…", progress=80)
    try:
        openapi_yaml = build_openapi_spec(schema)
        yield sse("openapi", "OpenAPI spec ready", payload=openapi_yaml, progress=87)
    except Exception as exc:
        yield sse("error", f"OpenAPI build failed: {exc}", progress=80)
        return

    # Step 7 — Rork Prompt ──────────────────────────────────────────────────────
    yield sse("step", "Writing Rork integration prompt…", progress=91)
    try:
        rork_user = (
            f"App: {schema.app_name}\n\n"
            f"Description: {req.description}\n\n"
            "API base URL: http://localhost:8000\n\n"
            f"Available routes:\n{build_route_list(schema)}\n\n"
            "Write a 150-250 word prompt the developer pastes into Rork to wire their "
            "existing screens to these real endpoints. Be actionable, reference actual "
            "route paths, and tell Rork exactly how to replace any mock data with "
            "fetch() calls to the real API."
        )
        msg = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            system=_RORK_SYSTEM,
            messages=[{"role": "user", "content": rork_user}],
        )
        rork_prompt = msg.content[0].text.strip()
        yield sse("back_to_rork", "Rork prompt ready", payload=rork_prompt, progress=97)
    except Exception as exc:
        yield sse("error", f"Rork prompt failed: {exc}", progress=91)
        return

    yield sse("done", "Generation complete!", progress=100)

# ── Router ──────────────────────────────────────────────────────────────────────

router = APIRouter()

@router.post("/generate")
async def generate(req: GenerateRequest) -> StreamingResponse:
    async def stream() -> AsyncGenerator[str, None]:
        async for chunk in generate_pipeline(req):
            yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream")
