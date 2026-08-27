---
name: fastapi-development
description: Build, review, debug, or upgrade FastAPI code for the Riwi Co. Internal Messaging Platform backend (Python 3.13, FastAPI 0.12x, psycopg 3, Pydantic v2) using current, non-deprecated conventions and the project's own Clean Architecture + RLS + JWT conventions. Trigger for any FastAPI/Pydantic/Python backend work in this repo: writing routes, use cases, repositories, providers, JWT middleware, BDD tests, or migrations touching backend code. Do NOT use for the React frontend (separate skill), for raw SQL DDL/DML in migrations (use the database skill), or for the AI provider SDK configuration (use the ai-provider-integration skill). FastAPI ships several releases per week and Pydantic v2 changed many idioms; treat this skill as required even when the task feels routine.
---

# FastAPI Development — Riwi Co. Messaging Platform

## Ground rule: this skill is Riwi Co.-specific

The project's [`/docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) is the source of truth for any architectural question. [`/AGENTS.md`](../AGENTS.md) defines prohibited actions and branching/PR norms. [`/docs/DECISIONS.md`](../docs/DECISIONS.md) is the decision log that justifies the architecture. When this skill and those documents disagree, **trust the documents** — they are reviewed in PR, the skill is not.

If a stack pin in this file drifts from `/backend/pyproject.toml` (or `requirements.txt`), treat the lockfile as authoritative and propose an upgrade PR before relying on the new version.

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

## Step 2: Project baseline (versions + tooling)

These are the contract from `ARCHITECTURE.md §12`. Don't drift without a deliberate upgrade PR.

| Package | Pin | Why |
|---|---|---|
| Python | **3.13** | First fully-settled 3.13 ecosystem; 3.14 free-threading still being validated across FastAPI/psycopg/testcontainers |
| FastAPI | **0.12x** (use `fastapi dev` / `fastapi run`) | Auto OpenAPI 3.x docs, async, DI; raw `uvicorn` only for custom deploys |
| `fastapi[standard]` | bundled with FastAPI | Pulls in Uvicorn + CLI tooling — preferred over bare `fastapi` |
| psycopg | **3.2.x** | SQL-first; **no ORM on the write path** (see Step 4) |
| pydantic | **2.x** | v2 idioms — `ConfigDict`, `model_dump`, `field_validator`, `model_validator` |
| pydantic-settings | latest 2.x | `BaseSettings` for `.env` config; never hand-parse env vars |
| pyjwt | latest | access + refresh JWT |
| argon2-cffi | latest | argon2id password hashing (bcrypt acceptable fallback) |
| httpx | latest | `TestClient` backend + OpenAI-compatible client for NVIDIA NIM |
| mistralai | latest SDK | Mistral embeddings adapter |
| pytest | **9.x** | BDD runner |
| pytest-bdd | latest | Gherkin feature files |
| testcontainers-python | **4.x** | Real PostgreSQL (image pinned to `pgvector/pgvector:pg18`) per test session |

Prefer **`uv`** for dependency management. Run the dev server with `fastapi dev`, not a raw `uvicorn app.main:app` invocation.

Use modern Python typing throughout: `str | None` not `Optional[str]`, `list[str]` not `List[str]`. The project targets 3.13 — the older syntax is never required.

## Step 3: Project layout — Clean Architecture

Per `ARCHITECTURE.md §5.2`. Backend code lives under `/backend/app/`, split by **layer**, not by route:

```text
backend/app/
├── domain/          # pure Python, NO FastAPI, NO psycopg, NO pydantic-settings
│   ├── entities/    # dataclasses / Pydantic models without I/O
│   ├── ports/       # Protocols: UserRepo, MessageRepo, ChannelRepo,
│   │                #           EmbeddingProvider, ChatProvider, TokenService, UnitOfWork
│   └── errors.py    # domain-level exceptions (ResourceNotFound, PermissionDenied, …)
├── application/     # use cases (commands + queries), one folder per feature
│   ├── auth/        # RegisterUser, Login, Refresh
│   ├── channels/    # CreateChannel, AddMember, ListVisibleChannels
│   ├── messages/    # SendMessage, EditMessage, DeleteMessage, ChannelHistory, SearchMessages
│   └── copilot/     # AskCopilot, CopilotUsage
├── infrastructure/  # adapters that implement the domain ports
│   ├── db/          # psycopg 3 repositories + RwSession + actor propagation
│   ├── auth/        # JwtService, Argon2Hasher, refresh rotation
│   └── ai/          # MistralAdapter, NvidiaAdapter
└── delivery/        # FastAPI wiring (the only layer that imports FastAPI)
    ├── http/
    │   ├── auth.py channels.py messages.py copilot.py
    └── middleware.py
```

Dependency rule (enforced by code review until a linter rule exists):

- **Domain** depends on nothing — no `fastapi`, no `psycopg`, no `pydantic_settings`.
- **Application** depends on Domain only — use cases take port objects, never `psycopg.Connection` or `fastapi.Request`.
- **Infrastructure** depends on Domain (implements the ports). Adapters live here, not in `delivery/`.
- **Delivery** depends on Application + Domain (wires FastAPI routers to use cases). Never calls repos directly.

## Step 4: Project-specific patterns

### 4.1 RLS-aware database sessions (the security boundary)

Every request opens **one** psycopg transaction, sets the actor via `SET LOCAL`, and runs every query inside it — including the copilot's vector search. The DB is the single security boundary (`ARCHITECTURE.md §3`); the backend is a thin dispatcher.

```python
# infrastructure/db/session.py
from contextlib import asynccontextmanager
import uuid
from psycopg_pool import AsyncConnectionPool

@asynccontextmanager
async def rw_session(pool: AsyncConnectionPool, actor_id: uuid.UUID):
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_user_id', %s, true)", str(actor_id)
            )
            yield conn
```

Use cases never see the connection pool — they receive a `UnitOfWork` port injected via the use-case constructor (DI). Repositories take a `RwSession`, not a raw connection, so the actor is always set.

Forbidden (from `AGENTS.md`):

- `SET ROLE bypassrls`, granting `BYPASSRLS`, or `ALTER ROLE ... SUPERUSER` on the application role.
- Any `f"… {var} …"` SQL string concatenation. Use **always** parameterized queries (`%s`, `$1`, `%(name)s`).
- Physical `DELETE` on `rw_message`. Logical delete via `rw_delete_message(...)` procedure.
- `OFFSET` / `LIMIT N OFFSET M` pagination. Keyset only (Step 4.6).
- Taking the user id from the request body. It comes from the verified JWT `sub`, period.

### 4.2 JWT + refresh-rotation middleware

`infrastructure/auth/` implements three ports: `JwtService`, `PasswordHasher`, `RefreshTokenStore`. The `RwAuthMiddleware`:

1. Reads `Authorization: Bearer <jwt>`.
2. Decodes + verifies with `JwtService.decode_access`. On failure: `401`.
3. Sets `request.state.actor_id` (a `uuid.UUID`, not a string).
4. For `/auth/refresh`: validates the refresh token, marks it revoked, issues a new pair, stores the new token hash under the same `rw_family_id`.

Refresh tokens are stored **hashed** in `rw_refresh_token` with `rw_family_id`. Presenting an already-revoked token revokes the **whole family** (reuse/theft detection) — see [Auth0 docs](https://auth0.com/docs/secure/tokens/refresh-tokens/refresh-token-rotation).

**Never** accept `user_id`, `actor_id`, or anything similar from a JSON body. The only acceptable sources are (a) the JWT `sub`, (b) server-issued path parameters derived from a row the actor can already see.

### 4.3 AI providers as ports (the `copilot` module)

`domain/ports/` declares:

```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

class ChatProvider(Protocol):
    async def chat(
        self, *, system: str, user: str,
        temperature: float = 0.2, model: str | None = None,
    ) -> str: ...
```

Adapters in `infrastructure/ai/`:

- **`MistralAdapter(EmbeddingProvider)`** — `mistralai` SDK; batch many bodies into one `embeddings.create(inputs=[...])` call (rate-limit friendly on the free tier).
- **`NvidiaAdapter(ChatProvider)`** — `httpx.AsyncClient` against `https://integrate.api.nvidia.com/v1/chat/completions` (OpenAI-compatible). Default model `mistralai/mistral-nemotron`, fallback `nvidia/nemotron-3.5-lightning-30b-a3b`. Model is config, not code (`ARCHITECTURE.md §4.3`).

Use cases depend on the protocols only. **No `import mistralai` or `import openai` outside `infrastructure/ai/`**. Every copilot call is followed by an insert into `rw_copilot_usage` (`model`, `prompt_tokens`, `completion_tokens`, `cost_usd`) — that insert is the audit trail (`ARCHITECTURE.md §4.2`).

The system prompt is a versioned constant in the repo and is logged per request. Retrieved messages are wrapped in explicit delimiters and labelled **UNTRUSTED** inside the prompt.

### 4.4 Idempotent send

```python
@router.post("/channels/{channel_id}/messages", status_code=201)
async def send_message(
    channel_id: uuid.UUID,
    payload: SendMessageIn,            # contains rw_client_ref
    actor: Annotated[Actor, Depends(get_actor)],
    uc: Annotated[SendMessage, Depends(get_send_message_uc)],
) -> MessageOut:
    return await uc.execute(channel_id=channel_id, payload=payload, actor=actor)
```

`SendMessage` calls the `rw_send_message(...)` DB function, which is idempotent on `(rw_author_id, rw_client_ref) WHERE rw_client_ref IS NOT NULL`. The 409 / 201 semantics live in the DB, not in Python. This is what backs the frontend's *pending → sent → failed* state machine.

### 4.5 HTTP status codes & error envelope

Per `ARCHITECTURE.md §6`:

- `200` reads · `201` registration · `204` logical delete
- `400` validation · `401` missing/invalid token · `404` **missing or invisible** (never `403` — would leak existence)
- Errors as RFC 9457 `application/problem+json` (uniform envelope)
- Every request gets/accepts `X-Request-Id`, echoed in response, error body, log
- Keyset pagination, never `OFFSET`

Use FastAPI's `HTTPException` with a custom `ProblemDetail` body, or a project-level exception handler in `delivery/middleware.py`. Do **not** return `{"detail": ...}` and `{"error": ...}` in different places — one envelope.

### 4.6 Keyset pagination (no `OFFSET`)

```sql
SELECT ... FROM rw_visible_message
WHERE rw_channel_id = $1
  AND ($2::timestamptz IS NULL
       OR (rw_created_at, rw_id) < ($2::timestamptz, $3::uuid))
ORDER BY rw_created_at DESC, rw_id DESC
LIMIT $4;
```

Cursor is opaque to the client; serialize as `{"created_at": ..., "id": ...}` and base64 it on the wire. Response: `{"items": [...], "next_cursor": {...}, "has_more": bool}`. `OFFSET` scans and discards N rows per page and skips/repeats rows when the list mutates between pages — keyset is stable under real-time delivery.

### 4.7 DB writes go through functions/procedures

The write path goes through transactional DB functions/procedures, not raw SQL in the application:

- `rw_register_user(...)` — creates `rw_user` + `rw_auth_credential` atomically.
- `rw_create_channel(...)` — creates channel + first member (creator as `owner`).
- `rw_send_message(...)` — inserts message; trigger fills `rw_embedding`.
- `rw_edit_message(...)` — appends `rw_message_edit`, updates body.
- `rw_delete_message(...)` — logical delete (`rw_deleted_at`, `rw_deleted_reason`).

The application layer's job is input validation and dispatch — **business rules live in the database**. This is the "thin use cases" rule from `ARCHITECTURE.md §5.1`.

## Step 5: Testing — BDD against real PostgreSQL

pytest-bdd + testcontainers-python. The two mandatory scenarios from `ARCHITECTURE.md §10` are the executable spec.

```python
# tests/features/membership.feature
Feature: Visible messages by channel membership
  Scenario: Non-member cannot see a private channel's messages
    Given user "Valentina" who is not a member of channel "Camila-private"
    And a message sent in "Camila-private" by user "Camila"
    When Valentina requests the channel history, a messages search, or asks the copilot
    Then the message does not appear in any of the three channels

# tests/conftest.py
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("pgvector/pgvector:pg18") as pg:
        yield pg.get_connection_url()
```

Each scenario sets `app.current_user_id` per actor (mirrors the manual `psql` verification the brief recommends doing first). Tests **must exercise the real RLS policy** — never override the application role or grant `BYPASSRLS` to the test role.

Use FastAPI's `TestClient` (built on `httpx`) with `app.dependency_overrides[...]` for repos/providers. For lifespan startup/shutdown, use `TestClient(app)` as a context manager so the lifespan actually runs.

## Step 6: Avoid deprecated/legacy patterns

Read `references/deprecated-patterns.md` for the full table of FastAPI/Pydantic patterns that still run but are deprecated, plus the project's own banned patterns (no `BYPASSRLS`, no SQL concatenation, no physical message delete, no `OFFSET`, no user-id-from-body). Treat that file as a checklist before opening a PR.

## Step 7: When in doubt, read the source of truth

- Architecture: `/docs/ARCHITECTURE.md`
- Prohibited actions + branch/PR norms: `/AGENTS.md`
- Decisions / rationale: `/docs/DECISIONS.md`
- FastAPI version specifics: [`fastapi.tiangolo.com/release-notes`](https://fastapi.tiangolo.com/release-notes/)
- Pydantic v2 specifics: [`docs.pydantic.dev/latest/migration`](https://docs.pydantic.dev/latest/migration/)
- psycopg 3: [`psycopg.org/psycopg3/docs`](https://www.psycopg.org/psycopg3/docs/)