# Deprecated / Legacy Patterns — FastAPI + Pydantic + psycopg (Riwi Co.)

Two categories:

1. **Framework deprecations** — patterns that still run but emit warnings, will eventually break, or have a strictly better modern replacement. Trigger these in training-data code all the time; flag them in code review.
2. **Project-banned patterns** — explicitly forbidden by [`/AGENTS.md`](../AGENTS.md) or [`/docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md). These are **hard fails**, not style nits — they will either fail CI (BDD test) or violate the security model.

If a pattern appears in **both** columns, treat it as project-banned.

---

## 1. Framework deprecations

| Instead of this | Write this | Why |
|---|---|---|
| `@app.on_event("startup")` / `@app.on_event("shutdown")` | `@asynccontextmanager` + `FastAPI(lifespan=...)` | `on_event` was deprecated in 0.93; lifespan is the only supported hook and handles graceful-shutdown signals correctly |
| `app.on_event("startup")` inside a `FastAPI()` factory | Define lifespan at the same place the app is built; pass to constructor | Same reason — keeps startup/shutdown co-located |
| `@validator("field")` / `@root_validator` | `@field_validator("field")` / `@model_validator(mode="after")` | v1 decorators; removed style in v2 |
| `class Config: orm_mode = True` | `model_config = ConfigDict(from_attributes=True)` | v1 inner-class config; replaced by `ConfigDict` |
| `class Config: anystr_lower = True` / `schema_extra = {...}` | `model_config = ConfigDict(str_strip_whitespace=True, json_schema_extra={...})` | Renames and replacements |
| `Model.dict()` | `Model.model_dump()` | v1 method; `dict` collides with `BaseModel.__dict__` |
| `Model.dict(exclude_unset=True, exclude_none=True)` | `Model.model_dump(exclude_unset=True, exclude_none=True)` | Same kwargs, new method |
| `Model.json()` | `Model.model_dump_json()` | v1 method |
| `Model.parse_obj(payload)` | `Model.model_validate(payload)` | v1 entrypoint |
| `Model.parse_raw(raw_json)` | `Model.model_validate_json(raw_json)` | v1 entrypoint |
| `Model.from_orm(obj)` | `Model.model_validate(obj)` with `from_attributes=True` in config | v1 bridge for ORM objects |
| `Model.schema()` / `Model.schema_json()` | `Model.model_json_schema()` | v1 schema accessors |
| `Optional[X]` / `Union[X, None]` | `X \| None` | PEP 604 — Python 3.10+; project is on 3.13, always the modern form |
| `List[X]` / `Dict[K, V]` / `Tuple[X, ...]` | `list[X]` / `dict[K, V]` / `tuple[X, ...]` | PEP 585 — builtins accept generics; project targets 3.13 |
| `@app.get(regex=r"...")` | `@app.get(path="/items/{item_id}")` with `Path(..., pattern=r"...")` (Pydantic-style) | `regex=` deprecated since 0.96 |
| `@app.get(regex=...)` (legacy) | `Annotated[str, Path(pattern=r"...")]` | Modern equivalent |
| `q: str = Query(default=None, max_length=50)` | `q: Annotated[str \| None, Query(max_length=50)] = None` | `Annotated` is the current docs default; reuse type aliases |
| `Depends(get_db)` as default value | `Annotated[Db, Depends(get_db)]` | Same reason |
| `@app.exception_handler(HTTPException)` that re-raises or swallows | Custom exception handler on `app.add_exception_handler(MyError, handler)` returning a `ProblemDetail` | Uniform RFC 9457 envelope per `ARCHITECTURE.md §6` |
| Calling sync `requests.get(...)` or `psycopg2.connect()` inside an `async def` handler | Use `httpx.AsyncClient` / `psycopg.AsyncConnection`; or declare the handler as plain `def` so FastAPI runs it in the threadpool | Sync calls in async handlers block the event loop — invisible at low traffic, catastrophic at scale |
| Hand-rolled `StreamingResponse` with manual `data: ...\n\n` SSE framing | `fastapi.sse.StreamingResponse` / `EventSourceResponse` (check installed version per the skill's Step 1) | Built-in helpers handle wire format correctly |
| `BackgroundTasks` for long / retryable / durable work | Real task queue (Celery / arq / RQ) | `BackgroundTasks` is fire-and-forget only; survives only until the process dies |
| `app.openapi()` custom builder that hand-writes JSON | Subclass `FastAPI` and override `openapi()` only if you must add components; otherwise let FastAPI generate from the route decorators | Hand-rolled OpenAPI drifts from the routes |
| Manual JWT secret comparison with `==` | `hmac.compare_digest(...)` or `jwt.decode(..., options={"verify_signature": True})` | Constant-time + library-level verification |

---

## 2. Project-banned patterns (per `AGENTS.md` + `ARCHITECTURE.md`)

These are not deprecated by the framework — they are **forbidden by the project**. Flag them in PR review regardless of how clean they look.

| Banned | Use instead | Where it's decided |
|---|---|---|
| `ALTER ROLE rw_app BYPASSRLS`, `SET ROLE bypassrls`, granting `SUPERUSER` to the app role | Keep the app role non-`BYPASSRLS`; visibility is enforced via RLS policies and `SET LOCAL app.current_user_id` | `AGENTS.md` Prohibited Actions; `ARCHITECTURE.md §3` |
| `f"SELECT ... WHERE id = {user_id}"` style SQL | `SELECT ... WHERE id = $1` / `WHERE id = %(id)s` — **always** parameterized. No string concatenation, no f-strings into SQL | `AGENTS.md` Prohibited Actions |
| ORM (SQLAlchemy ORM, Tortoise, Piccolo) on the **write path** | Raw psycopg 3 with the DB function/procedure call. SQLAlchemy Core is acceptable for read-only convenience if/where it adds value | `ARCHITECTURE.md §5` and §12 |
| `DELETE FROM rw_message WHERE ...` (physical delete) | `CALL rw_delete_message(...)` (procedure that sets `rw_deleted_at` + `rw_deleted_reason`) — logical delete only | `AGENTS.md` Prohibited Actions; `ARCHITECTURE.md §2.5` |
| `LIMIT N OFFSET M` pagination | Keyset pagination: `WHERE (created_at, id) < (cursor_ts, cursor_id) ORDER BY created_at DESC, id DESC LIMIT N` | `ARCHITECTURE.md §6` |
| Returning `{"detail": ...}` from some endpoints and `{"error": ...}` from others | One uniform RFC 9457 `application/problem+json` envelope via a project exception handler | `ARCHITECTURE.md §6` |
| `403 Forbidden` for "you cannot see this resource" | `404 Not Found` — `403` leaks that the row exists, which is itself a leak in a messaging system | `ARCHITECTURE.md §6` |
| Reading `user_id` / `actor_id` / `channel_id` from the **request body** for an authorization decision | Only the verified JWT `sub`. Body fields may carry **content** (e.g. `client_ref`, `body`), never authorization claims | `AGENTS.md` Security notes; `ARCHITECTURE.md §7` |
| Sending a raw password to the API in any form other than argon2id-hashed on the server | `argon2-cffi` (`argon2id`) at registration; `bcrypt` acceptable as fallback. Never MD5/SHA | `ARCHITECTURE.md §7` |
| Storing refresh tokens in plaintext | Store **only the hash** in `rw_refresh_token.rw_token_hash` (with `rw_family_id`); never the raw token | `ARCHITECTURE.md §7` |
| Issuing a new access token without rotating the refresh token on use | Rotate the refresh token on **every** `/auth/refresh`; revoke the previous; reuse-detection via `rw_family_id` | `ARCHITECTURE.md §7`; Auth0 refresh-token-rotation pattern |
| Calling the Mistral SDK or the OpenAI client directly from a use case | Go through the `EmbeddingProvider` / `ChatProvider` port; the model name is config | `ARCHITECTURE.md §4.3` and §5.2 |
| Logging or echoing raw JWTs, refresh tokens, or passwords | Log only the actor id (UUID), never token bytes; redact `Authorization` and `Cookie` headers in middleware | `AGENTS.md` Prohibited Actions (no secrets in logs) |
| Hardcoding model names (`mistral-embed`, `mistralai/mistral-nemotron`) inside use cases | Read from `Settings` (`pydantic-settings`) loaded from `.env`; pass through the provider constructor | `ARCHITECTURE.md §4.3` |
| Setting a global module-level connection pool that ignores `actor_id` per request | `RwSession` context manager that opens a transaction and sets `app.current_user_id` **inside** the transaction — so pooling (Neon/Supabase transaction mode) never separates the two | `ARCHITECTURE.md §3` and §11.1 |
| Letting a single request run queries across more than one transaction | Open **one** transaction per request; set the actor once; close on response | `ARCHITECTURE.md §7` |

---

## How to use this file

1. Before opening a PR that touches FastAPI/Pydantic code, scan column 1 for any pattern you wrote.
2. If you find one you genuinely need (e.g. you're porting legacy code), write a comment justifying it and link to the replacement column.
3. Project-banned entries (column 2) are non-negotiable. Don't propose exceptions in the PR — open an issue against `ARCHITECTURE.md` and update the source of truth first.

If a deprecated/banned pattern isn't listed here, it doesn't mean it's allowed — Step 1 of `SKILL.md` (live version check) and the source-of-truth docs (`ARCHITECTURE.md`, `AGENTS.md`) are the final word.