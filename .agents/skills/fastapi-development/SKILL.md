---
name: fastapi-development
description: Build, review, debug, or upgrade FastAPI code for the Riwi Co. Internal Messaging Platform backend (Python 3.13, FastAPI 0.12x, psycopg 3, Pydantic v2) using current, non-deprecated conventions and the project's own Clean Architecture + RLS + JWT conventions. Trigger for any FastAPI/Pydantic/Python backend work in this repo: writing routes, use cases, repositories, providers, JWT middleware, BDD tests, or migrations touching backend code. Do NOT use for the React frontend (separate skill), for raw SQL DDL/DML in migrations (use the database skill), or for the AI provider SDK configuration (use the ai-provider-integration skill). FastAPI ships several releases per week and Pydantic v2 changed many idioms; treat this skill as required even when the task feels routine.
---

# FastAPI Development — Riwi Co. Messaging Platform

> **Skill maintenance notice (verified 2026-08-29).** Per
> [`/AGENTS.md`](../AGENTS.md) "Skill Maintenance": this skill no
> longer carries predictive code blocks — every code path below
> references the shipped file directly. If this file contradicts
> `/backend/app/*.py`, the source wins.

## Ground rule: this skill is Riwi Co.-specific

The project's [`/docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) is the source of truth for any architectural question. [`/AGENTS.md`](../AGENTS.md) defines prohibited actions and branching/PR norms. [`/docs/DECISIONS.md`](../docs/DECISIONS.md) is the decision log that justifies the architecture. When this skill and those documents disagree, **trust the documents** — they are reviewed in PR, the skill is not.

If a stack pin in this file drifts from `/backend/pyproject.toml` (or `uv.lock`), treat the lockfile as authoritative and propose an upgrade PR before relying on the new version.

## Why this skill exists

Two reasons:

1. **FastAPI moves fast and Pydantic v2 rewrote the idioms.** Training data routinely emits `@app.on_event`, `regex=` on `Query`/`Path`, `class Config:`, `.dict()` / `.json()`, `@validator` — all deprecated or replaced. The full deprecation table is in `references/deprecated-patterns.md`; the modern equivalents in `references/modern-patterns.md`.
2. **The backend has hard architectural rules that are easy to violate by accident.** RLS bypass, ORM on the write path, `OFFSET` pagination, physical `DELETE`, user id taken from the request body — every one of these is a hard fail per `AGENTS.md` and a 30-second test in BDD. Catching them at the SQL prompt or the test suite is the right place; catching them in code review is expensive.

## Step 1: Pin the installed versions, cheaply

Anything version-sensitive (new features, new deprecations) needs a live check:

```bash
python -c "import importlib.metadata as m; print('fastapi', m.version('fastapi')); print('pydantic', m.version('pydantic')); print('psycopg', m.version('psycopg'));"
```

If installed versions are >2 minor behind the latest stable, scan [`fastapi.tiangolo.com/release-notes`](https://fastapi.tiangolo.com/release-notes/) for `Breaking Changes` between the installed and the latest. Same for [Pydantic](https://docs.pydantic.dev/latest/migration/) and [psycopg 3](https://www.psycopg.org/psycopg3/docs/).

**Last verified:** fastapi 0.141.1, pydantic 2.13.4, pydantic-settings 2.15.0, pyjwt 2.13.0, argon2-cffi 25.1.0, psycopg 3.2.x. Pinned via [`/backend/pyproject.toml`](../../backend/pyproject.toml) — confirm against `uv.lock` before relying on this snapshot.

## Step 2: Project baseline (versions + tooling)

These are the contract from `ARCHITECTURE.md §12`. Don't drift without a deliberate upgrade PR.

| Package | Pin | Why |
|---|---|---|
| Python | **3.13** | First fully-settled 3.13 ecosystem; 3.14 free-threading still being validated across FastAPI/psycopg/testcontainers |
| FastAPI | **0.12x** (use `fastapi dev` / `fastapi run`) | Auto OpenAPI 3.x docs, async, DI; raw `uvicorn` only for custom deploys |
| `fastapi[standard]` | bundled with FastAPI | Pulls in Uvicorn + CLI tooling — preferred over bare `fastapi` |
| psycopg | **3.2.x** | SQL-first; **no ORM on the write path** (see Step 4) |
| pydantic | **2.x** | v2 idioms — `ConfigDict`, `model_dump`, `field_validator`, `model_validator` |
| pyjwt | latest | access + refresh JWT |
| argon2-cffi | latest | argon2id password hashing (bcrypt acceptable fallback) |
| httpx | latest | `TestClient` backend + OpenAI-compatible client for NVIDIA NIM |
| mistralai | latest SDK | Mistral embeddings adapter |
| pytest | **9.x** | BDD runner |
| pytest-bdd | latest | Gherkin feature files |
| testcontainers-python | **4.x** | Real PostgreSQL (image pinned to `pgvector/pgvector:pg18`) per test session |

Prefer **`uv`** for dependency management. Run the dev server with `fastapi dev`, not a raw `uvicorn app.main:app` invocation.

Use modern Python typing throughout: `str | None` not `Optional[str]`, `list[str]` not `List[str]`. The project targets 3.13 — the older syntax is never required.

## Step 3: Project layout — the shipped flat structure

Per `ARCHITECTURE.md §5.2`. Backend code lives under `/backend/app/`, **flat** (one file per concern, no nested `domain/` / `application/` / `infrastructure/` / `delivery/` directories):

```text
backend/app/
├── __init__.py
├── config.py        # Settings — @dataclass(frozen=True) + from_env() (NOT pydantic-settings)
├── domain.py        # entities (User, Channel, RefreshTokenRecord, …) + ports (Protocols)
│                    #   - PasswordHasher, JwtService, RefreshTokenStore
│                    #   - UserRepository, ChannelRepository, ChannelMemberRepository
│                    #   - MessageRepository, SearchRepository
│                    #   - EmbeddingProvider, ChatProvider, CopilotUsageRepository
│                    #   - SessionFactory, TransientProviderError, ProviderError
├── auth.py          # RegisterUser, Login, Refresh use cases + TokenPair + AuthError
├── channels.py      # CreateChannel, AddMember, LeaveChannel, ListVisibleChannels + ChannelError
├── messages.py      # SendMessage, EditMessage, DeleteMessage, ChannelHistory,
│                    #   SearchMessages, MarkChannelRead, MarkRead, UnreadCountForChannel
├── copilot.py       # AskCopilot use case + four DENY_* constants + CopilotError
├── copilot_prompt.py # PROMPT_VERSION + BASE_SYSTEM_PROMPT (versioned constant)
├── infrastructure.py # Argon2idHasher, PyJwtService, RefreshTokenHasher,
│                    #   RwSession, Postgres* adapters, MistralAdapter, NvidiaAdapter,
│                    #   PostgresCopilotUsageRepository, fetch_copilot_usage_summary
├── delivery.py      # JwtAuthMiddleware + get_current_actor + build_auth_router /
│                    #   build_channels_router / build_messages_router / build_copilot_router /
│                    #   build_me_router + _STATUS_MAP
└── main.py          # create_app(settings=..., session_factory=..., cors_origins=...,
                     #                embedder=..., chatter=...) + middleware wiring
```

> **The flat layout is intentional.** Phase 2 chose it; Phase 7 still uses it. The brief's scope did not justify the import-graph move to a nested layout. Don't propose splitting these files in a PR without an issue that names the concrete benefit (e.g. "this file is now > 800 lines and the import surface is fighting").

Dependency rule (enforced by code review until a linter rule exists):

- **Domain** (`domain.py`) depends on nothing — no `fastapi`, no `psycopg`, no `pydantic_settings`. Pure Python + `typing.Protocol`.
- **Application** (`auth.py`, `channels.py`, `messages.py`, `copilot.py`) depends on Domain only — use cases take port objects, never `psycopg.Connection` or `fastapi.Request`.
- **Infrastructure** (`infrastructure.py`) depends on Domain (implements the ports). Adapters live here.
- **Delivery** (`delivery.py`, `main.py`) depends on Application + Domain (wires FastAPI routers to use cases). Never calls repos directly.

## Step 4: Project-specific patterns

### 4.1 RLS-aware database sessions (the security boundary)

Every request opens **one** psycopg transaction, sets the actor via `SET LOCAL`, and runs every query inside it — including the copilot's vector search. The DB is the single security boundary (`ARCHITECTURE.md §3`); the backend is a thin dispatcher.

See [`/backend/app/infrastructure.py`](../../backend/app/infrastructure.py) — `RwSession` is a sync `__enter__/__exit__` context manager (not async — Phase 2 keeps the simple `psycopg.Connection` shape; Phase 7 swaps to `psycopg_pool.AsyncConnectionPool`). It calls `set_config('app.current_user_id', %s, true)` on entry and commits or rolls back on exit.

Use cases never see the connection pool — they receive a `SessionFactory` callable injected via the use-case constructor (DI). Repositories take a `psycopg.Connection` and the use case constructs an adapter (`PostgresUserRepository(conn)`) inside the `RwSession` block so the actor is always set.

Forbidden (from `AGENTS.md`):

- `SET ROLE bypassrls`, granting `BYPASSRLS`, or `ALTER ROLE ... SUPERUSER` on the application role.
- Any `f"… {var} …"` SQL string concatenation. Use **always** parameterized queries (`%s`, `$1`, `%(name)s`).
- Physical `DELETE` on `rw_message`. Logical delete via `rw_delete_message(...)` procedure.
- `OFFSET` / `LIMIT N OFFSET M` pagination. Keyset only (Step 4.6).
- Taking the user id from the request body. It comes from the verified JWT `sub`, period.

### 4.2 JWT + refresh-rotation middleware

See [`/backend/app/delivery.py`](../../backend/app/delivery.py) (`JwtAuthMiddleware`) and [`/backend/app/auth.py`](../../backend/app/auth.py) (`Refresh` use case). Summary of the shipped behavior:

1. `JwtAuthMiddleware` reads `Authorization: Bearer <jwt>`.
2. Decodes + verifies with `PyJwtService.decode_access` (PyJWT HS256). On PyJWT error: leaves `request.state.actor_id = None` — the route's `Depends(get_current_actor)` enforces 401.
3. Sets `request.state.actor_id` (a `uuid.UUID`, not a string).
4. `Refresh` use case validates the presented refresh token (hashed SHA-256 of the plaintext, looked up via the `rw_find_refresh_token(...)` SECURITY DEFINER function — direct table access is REVOKEd from `rw_app`, see migration 0140). On happy path: revoke the old row (via `rw_revoke_refresh_token(...)`), insert a new row under the **same** `rw_family_id`. On reuse (token already revoked): one SQL `UPDATE … WHERE rw_family_id = %s` (via `rw_revoke_refresh_token_family(...)`) revokes every remaining row in the family.

The reuse-detection path MUST `conn.commit()` **before** raising `AuthError` — otherwise `RwSession.__exit__` rolls back the security write and the family stays open. Covered by the BDD scenario in [`/backend/tests/features/auth.feature`](../../backend/tests/features/auth.feature) (`Reusing a revoked refresh token revokes the entire family`) and the unit test `test_reuse_detection_revokes_entire_family` in `tests/unit/application/auth/test_use_cases.py`.

Refresh tokens are stored **hashed** in `rw_refresh_token` with `rw_family_id`. Presenting an already-revoked token revokes the **whole family** (reuse/theft detection) — see [Auth0 docs](https://auth0.com/docs/secure/tokens/refresh-tokens/refresh-token-rotation).

**Never** accept `user_id`, `actor_id`, or anything similar from a JSON body. The only acceptable sources are (a) the JWT `sub`, (b) server-issued path parameters derived from a row the actor can already see. The unit test `test_access_jwt_carries_sub_only` enforces this in code (asserts no `role` / `channel_ids` / `permissions` claims are present in the issued JWT).

### 4.3 Idempotent send

```python
@router.post("/channels/{channel_id}/messages", status_code=201)
async def send_message(
    channel_id: uuid.UUID,
    payload: SendMessageIn,            # contains rw_client_ref
    actor: Annotated[Actor, Depends(get_current_actor)],
    uc: Annotated[SendMessage, Depends(get_send_message_uc)],
) -> MessageOut:
    return await uc.execute(channel_id=channel_id, payload=payload, actor=actor)
```

`SendMessage` calls the `rw_send_message(...)` DB function, which is idempotent on `(rw_author_id, rw_client_ref) WHERE rw_client_ref IS NOT NULL`. The 409 / 201 semantics live in the DB, not in Python. This is what backs the frontend's *pending → sent → failed* state machine.

### 4.4 HTTP status codes & error envelope

Per `ARCHITECTURE.md §6`:

- `200` reads · `201` registration · `204` logical delete
- `400` validation · `401` missing/invalid token · `404` **missing or invisible** (never `403` — would leak existence). See `_STATUS_MAP` in `/backend/app/delivery.py` for the current set of codes (the entry for `'not-author': 403` was removed in PR #33 because nothing raises that code — non-author edits / deletes silently return 404).
- Errors as RFC 9457 `application/problem+json` (uniform envelope) — *not yet implemented*; tracked under issue #21.
- Every request gets/accepts `X-Request-Id`, echoed in response, error body, log — *not yet implemented*; tracked under issue #25.
- Keyset pagination, never `OFFSET`

Use FastAPI's `HTTPException` with a custom `ProblemDetail` body, or a project-level exception handler in `delivery.py`. Do **not** return `{"detail": ...}` and `{"error": ...}` in different places — one envelope.

### 4.5 Keyset pagination (no `OFFSET`)

```sql
SELECT ... FROM rw_visible_message
WHERE rw_channel_id = $1
  AND ($2::timestamptz IS NULL
       OR (rw_created_at, rw_id) < ($2::timestamptz, $3::uuid))
ORDER BY rw_created_at DESC, rw_id DESC
LIMIT $4;
```

Cursor is opaque to the client; serialize as `{"created_at": ..., "id": ...}` and base64 it on the wire. Response: `{"items": [...], "next_cursor": {...}, "has_more": bool}`. `OFFSET` scans and discards N rows per page and skips/repeats rows when the list mutates between pages — keyset is stable under real-time delivery.

### 4.6 DB writes go through functions/procedures

The write path goes through transactional DB functions/procedures, not raw SQL in the application:

- `rw_register_user(...)` — creates `rw_user` + `rw_auth_credential` atomically (Phase 1, 0040).
- `rw_create_channel(...)` — creates channel + first member (creator as `owner`) (Phase 1, 0040).
- `rw_add_channel_member(...)` — channel owner-only invite; SECURITY DEFINER (Phase 3, 0100). The `rw_channel_member` RLS policy (`rw_user_id = GUC`) lets the actor only see / modify their own membership rows, so adding a *different* user requires the function.
- `rw_send_message(...)` — inserts message; trigger fills `rw_embedding` (Phase 1, 0040). **Phase 4 (0110) added an `out_was_replay` OUT parameter** so the application can distinguish a fresh insert from an idempotent replay (same `client_ref`) without inspecting timestamps. The route surfaces 201 vs 200 based on this flag; the frontend's *pending → sent → failed* state machine uses the 200 + `X-Idempotent-Replay: true` response to dedupe.
- `rw_edit_message(...)` — appends `rw_message_edit`, updates body (Phase 1, 0040; procedure). **PR #33 added an explicit author gate inside the procedure body** because the procedure runs as the function owner (which has `BYPASSRLS` *if also `SUPERUSER`*) — without the gate, a non-author could overwrite someone else's message. See `DECISIONS.md` for the lesson learned.
- `rw_delete_message(...)` — logical delete (`rw_deleted_at`, `rw_deleted_reason`) (Phase 1, 0040; procedure).
- `rw_insert_refresh_token(...)`, `rw_find_refresh_token(...)`, `rw_revoke_refresh_token(...)`, `rw_revoke_refresh_token_family(...)` — Phase 7 (0140) SECURITY DEFINER wrappers around `rw_refresh_token`. The runtime role has no direct table privileges.
- `rw_record_copilot_usage(...)` — Phase 7 (0140) SECURITY DEFINER wrapper for the §11.4 audit insert.

See [`/backend/db/migrations/0100_rw_add_channel_member.sql`](../../db/migrations/0100_rw_add_channel_member.sql), [`/backend/db/migrations/0110_rw_send_message_replay_flag.sql`](../../db/migrations/0110_rw_send_message_replay_flag.sql), and [`/backend/db/migrations/0140_rls_on_user_scoped_tables.sql`](../../db/migrations/0140_rls_on_user_scoped_tables.sql) for the SECURITY DEFINER patterns. The application layer's job is input validation and dispatch — **business rules live in the database**. This is the "thin use cases" rule from `ARCHITECTURE.md §5.1`.

### 4.7 CORS for the Vite dev server

The Vite dev server lives on a different origin (default `http://localhost:5173`) than the FastAPI backend (`http://localhost:8000`). `CORSMiddleware` is added to `create_app` in [`/backend/app/main.py`](../../backend/app/main.py) with the allow-list coming from `Settings.cors_origins` (`RW_CORS_ORIGINS` env var, comma-separated) — **no hardcoded ports** (per `docs/DECISIONS.md`). The `cors_origins` parameter lets tests pass an empty list to disable CORS for in-process calls.

### 4.8 Dev entrypoint

[`/backend/dev_app.py`](../../backend/dev_app.py) wraps `create_app(Settings.from_env())` for `uvicorn dev_app:app`. Use:

```bash
RW_DATABASE_URL="postgresql://rw_app_login:dev_app_pwd@localhost:5433/db_santiago_sanchez_nakamoto" \
  ./.venv/bin/python -m uvicorn dev_app:app --host 0.0.0.0 --port 8000
```

Tests use `create_app(settings=..., session_factory=..., embedder=..., chatter=...)` directly; `dev_app.py` is the dev-server seam.

> **No DI container.** `create_app` is a factory function whose parameters are the injection seams (`session_factory`, `embedder`, `chatter`). Tests in `conftest.py` pass a testcontainer-backed factory + `FakeEmbeddingProvider` / `FakeChatProvider`. Production leaves `embedder` / `chatter` as `None` and `main.py` builds the real adapters from `settings`. Don't introduce `dishka` / `punq` / `dependency-injector` in a PR without an issue that names the concrete benefit (the current pattern is five parameters and works).

### 4.9 Search + mark-channel-read endpoints (Phase 5)

Two new routes on the messages router (Phase 5, issue #9):

- `GET /api/v1/channels/{channel_id}/search?q=&limit=` — `SearchMessages` use case. The DB function `rw_search_messages(...)` does the heavy lifting (locale from `rw_user.rw_locale`, `ts_headline` with `<mark>` tags, RLS-bypass defense-in-depth check). The route validates `q` 1..200 chars and `limit` 1..50. Returns `{ items: [{rw_id, rw_channel_id, rw_author_id, rw_body, rw_created_at, rw_highlight, is_mine}, …] }`.
- `POST /api/v1/channels/{channel_id}/read` — `MarkChannelRead` use case. Bulk-marks every visible message as read for the actor. Idempotent. Returns `{ inserted: <n> }` (the count of newly-inserted rows; useful for the API response shape but currently unused by the frontend).

The channel list endpoint (`GET /api/v1/channels`) gained an `unread_count: int` field per channel — backed by `rw_unread_count_for_channel(channel_id, user_id)` called once per channel inside `PostgresChannelRepository.list_visible_with_unread`. The frontend renders the per-channel badge + a total badge in the header. **Keyset pagination on `GET /api/v1/channels` is NOT shipped** — tracked under issue #27.

See [`/backend/app/delivery.py:build_messages_router`](../../backend/app/delivery.py) — the Phase 4 `SearchHitOut` + `MarkChannelReadOut` Pydantic models are declared inside `build_messages_router` (the pattern is "wire shape next to the route that returns it"). For more on the DB-side contract, see `Step 9.7` of the postgres-rls-pgvector skill.

## Step 5: Testing — BDD against real PostgreSQL

pytest-bdd + testcontainers-python. The two mandatory scenarios from `ARCHITECTURE.md §10` are the executable spec.

**See the shipped fixture:** [`/backend/tests/conftest.py`](../../backend/tests/conftest.py) — one-line summary: `pg_container` (session-scoped `PostgresContainer("pgvector/pgvector:pg18")`), `_bootstrap` (applies migrations + creates `rw_app_login` with the test password, *skipping* `0002_roles.sql`), `super_conn` (superuser — setup only), `actor_conn` (`rw_app_login` — every read-and-assert goes here), `pg_super_url` / `pg_app_url`. Each scenario sets the actor GUC per request via `RwSession` (mirrors the production code path exactly). **Tests never connect as a `BYPASSRLS` role for the query under test** — that's the whole point of the exercise.

Use FastAPI's `TestClient` (built on `httpx`) with the `create_app(settings=..., session_factory=..., embedder=..., chatter=...)` factory. For the `embedder` / `chatter` injection in tests, see [`/backend/tests/fake_chat_provider.py`](../../backend/tests/fake_chat_provider.py) — `FakeEmbeddingProvider`, `FakeChatProvider`, and `_reset_default()`.

## Step 6: Avoid deprecated/legacy patterns

Read `references/deprecated-patterns.md` for the full table of FastAPI/Pydantic patterns that still run but are deprecated, plus the project's own banned patterns (no `BYPASSRLS`, no SQL concatenation, no physical message delete, no `OFFSET`, no user-id-from-body). Treat that file as a checklist before opening a PR.

## Step 7: When in doubt, read the source of truth

- Architecture: `/docs/ARCHITECTURE.md`
- Prohibited actions + branch/PR norms: `/AGENTS.md`
- Decisions / rationale: `/docs/DECISIONS.md`
- FastAPI version specifics: [`fastapi.tiangolo.com/release-notes`](https://fastapi.tiangolo.com/release-notes/)
- Pydantic v2 specifics: [`docs.pydantic.dev/latest/migration`](https://docs.pydantic.dev/latest/migration/)
- psycopg 3: [`psycopg.org/psycopg3/docs`](https://www.psycopg.org/psycopg3/docs/)