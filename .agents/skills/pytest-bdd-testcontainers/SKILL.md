---
name: pytest-bdd-testcontainers
description: Write, run, and debug Behaviour-Driven Development tests for the Riwi Co. Messaging Platform backend using pytest, pytest-bdd (Gherkin feature files), and testcontainers-python against a real `pgvector/pgvector:pg18` PostgreSQL instance. Trigger for any work under /backend/tests/ or /db/tests/, for any new feature file (.feature), step definition, scenario, or fixture, and for the two mandatory BDD scenarios that prove RLS works (visible-messages-by-membership + author-always-sees-own). Use for container lifecycle (Postgres, Redis, Mistral emulator), fixture composition, Gherkin best practices, `app.current_user_id` per-actor setup, and CI gating. Do NOT use for plain unit tests on pure Python (use the fastapi-development skill), for raw SQL/RLS work (use postgresql-rls-pgvector), or for AI provider behavior tests (use ai-provider-integration).
---

# pytest + pytest-bdd + testcontainers — Riwi Co. Messaging Platform

## Ground rule: the security tests must exercise the real RLS policy

Per [`/docs/ARCHITECTURE.md §10`](../docs/ARCHITECTURE.md), the two mandatory scenarios are *executable specifications* of the security model. They MUST run against a **real PostgreSQL** (via testcontainers), with the **real `pgvector` extension**, executing as the **`rw_app` role** without `BYPASSRLS`. Mocking the DB, granting `BYPASSRLS`, or overriding RLS in a test fixture defeats the point of the test — the whole guarantee is that the same policy applies to the backend, the seed script, and `psql` as a DBA.

If a test needs data the RLS policy would hide, set `app.current_user_id` to the actor that *should* see it. Do not bypass RLS.

## Project baseline

| Item | Pin | Why |
|---|---|---|
| pytest | **9.x** | Current stable |
| pytest-bdd | latest | Gherkin feature files in `.feature` |
| pytest-asyncio | latest | `async def` step definitions + use-case tests |
| httpx | latest | FastAPI `TestClient` backend |
| testcontainers-python | **4.x** | Real services in Docker, scoped per test session |
| Postgres image | **`pgvector/pgvector:pg18`** (pinned in the fixture) | Matches `ARCHITECTURE.md §11` |
| Docker | required for local runs; CI provides it | testcontainers shells out to `docker` |
| Test database role | `rw_app` (no `BYPASSRLS`), `rw_migrator` (DDL only) | Same roles as production |
| Test runner | `uv run pytest -q tests/` | One command; no Makefile dance |
| Coverage gate | 80% for `domain/` + `application/`; 60% for `infrastructure/` | `domain/` is pure Python — should be 100% |

Layout (per `ARCHITECTURE.md §11`):

```
tests/
├── conftest.py                 -- testcontainers fixture, app fixture, settings override
├── features/
│   ├── membership.feature       -- the two mandatory BDD scenarios
│   ├── auth.feature             -- register / login / refresh rotation
│   ├── messages.feature         -- send / edit / delete / search
│   └── copilot.feature          -- ask + citation + denial taxonomy
├── step_defs/                   -- one .py per .feature, mirroring the layout
│   ├── membership_steps.py
│   ├── auth_steps.py
│   ├── messages_steps.py
│   └── copilot_steps.py
├── unit/                        # plain pytest functions, no feature file
│   ├── domain/
│   ├── application/
│   └── infrastructure/
└── e2e/                         # full HTTP round-trips via TestClient + real DB
```

## Step 1: The session-scoped Postgres fixture

The Postgres container is started **once per test session** — not once per test — because spinning up a container takes 2–5 seconds and the migrations are expensive. Tests share the database but each test gets a clean schema (or a transactional rollback).

**See the shipped fixture:** [`/backend/tests/conftest.py`](../../backend/tests/conftest.py) — one-line summary: `pg_container` (session-scoped `PostgresContainer("pgvector/pgvector:pg18")`), `_bootstrap` (applies migrations + creates `rw_app_login` with the test password), and `pg_super_url` / `pg_app_url` (the two URLs the rest of the suite uses). Migrations are sourced from `/db/migrations/*.sql` and 0001 + 0002 are skipped (the testcontainer ships `pgcrypto`/`vector`; roles are created with a test-only password). Per-test rollback vs truncate lives in the `super_conn` and `actor_conn` fixtures.

A session-scoped container + per-test transaction rollback is the fastest realistic pattern; see `references/per-test-rollback.md` for the implementation.

## Step 2: The "act as" fixture (the RLS safety net)

Every test that touches business data must set `app.current_user_id` to the actor it's testing as. Never connect as a `postgres` superuser for the actual query under test — superusers bypass RLS by definition.

**See the shipped helper:** [`/backend/tests/conftest.py`](../../backend/tests/conftest.py) — `as_actor(conn, actor_id)` calls `set_config('app.current_user_id', %s, false)`; the `_seed` fixture (autouse) resets the dataset between tests via `TRUNCATE ... CASCADE`. The same module exports the canonical actor UUIDs (`VALENTINA`, `CAMILA`, `CHANNEL_PRIVATE`, `CHANNEL_TEAM1`) so step defs and the feature file stay in sync.

The test asserts the security guarantee, not the implementation. If you find yourself asserting on the absence of an `EXISTS` clause or a specific GUC name, you're testing the wrong layer.

## Step 3: The FastAPI app fixture

```python
# /backend/tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.infrastructure.settings import Settings

@pytest.fixture
def app(pg_url):
    """A FastAPI app wired to the testcontainers Postgres, with the AI provider faked."""
    settings = Settings(
        database_url=pg_url,
        mistral_api_key="test-key",
        nvidia_api_key="test-key",
        chat_model_primary="mistralai/mistral-nemotron",
    )
    application = create_app(settings=settings)
    # Replace AI adapters with fakes — see /backend/tests/unit/infrastructure/ai/fakes.py
    from tests.fakes import FakeEmbeddingProvider, FakeChatProvider
    application.container.override(FakeEmbeddingProvider(dim=1024))
    application.container.override(FakeChatProvider(text="fake answer [a1b2]"))
    return application

@pytest.fixture
def client(app):
    with TestClient(app) as c:        # context manager → lifespan runs
        yield c
```

The `application.container.override(...)` pattern depends on whatever DI mechanism you adopt (see `fastapi-development` skill §3 — likely `dishka`, `punq`, or hand-rolled). The point is: **the AI provider must never be a real network call in a unit/BDD test**.

## Step 4: The two mandatory BDD scenarios

These come straight from `ARCHITECTURE.md §10`. They are the executable spec for the security model — keep them green.

**See the shipped feature file:** [`/backend/tests/features/membership.feature`](../../backend/tests/features/membership.feature) — one-line summary: two scenarios (non-member cannot see private channel's messages across history / search / copilot; author always sees their own messages despite role changes) wired to the `_seed` fixture in `conftest.py`. Step definitions live in [`/backend/tests/step_defs/test_membership.py`](../../backend/tests/step_defs/test_membership.py) and are bound to the feature via `scenarios(str(_FEATURE_FILE))` (pytest-bdd 8.x removed auto-discovery).

Notes on the Gherkin:

- **Plain English, not implementation.** No `app.current_user_id`, no `rw_channel_member`, no SQL — the scenario should read like a product spec.
- **Concrete actors, not "user #1".** Names make test failures diagnostic.
- **Each `When`/`Then` pair is one assertion.** Don't pack 4 expectations into a single `Then`.
- **The copilot step uses the denial taxonomy** from `ai-provider-integration/references/denial-taxonomy.md`. If the wording drifts, both the prompt and the test must change together.

## Step 5: Step definitions (skeleton)

**See the shipped step defs:** [`/backend/tests/step_defs/test_membership.py`](../../backend/tests/step_defs/test_membership.py) — one-line summary: the canonical mapping between the Gherkin `Given/When/Then` and the SQL + RLS assertions. Phase 6 adds copilot-specific step defs and the `infer:low-confidence` scenario in the same file (the BDD lives next to the seed fixture so the dataset and assertions cannot drift).

## Step 6: Per-test isolation (rollback vs truncate)

Two patterns, pick one per test layer:

| Pattern | When to use | Trade-off |
|---|---|---|
| Transactional rollback (`BEGIN; ...; ROLLBACK;`) | Fast unit/BDD tests; no DDL inside the test | All test data must fit in one transaction; can't test commits |
| `TRUNCATE rw_message, rw_channel, ... RESTART IDENTITY CASCADE` | Tests that exercise commit semantics or COMMIT-after-INSERT flows | Slower; test order can leak if you forget a table |

For the two mandatory security scenarios, transactional rollback is enough — you're testing *visibility*, not commit semantics. For the auth flow (refresh-token rotation commits), use truncate.

```python
# /backend/tests/conftest.py — transactional rollback
@pytest.fixture
def db(pg_url):
    conn = psycopg.connect(pg_url, autocommit=False)
    yield conn
    conn.rollback()
    conn.close()
```

## Step 7: Faking the AI providers

```python
# /backend/tests/fakes.py
from app.domain.ports.ai_providers import ChatProvider, EmbeddingProvider, ChatUsage

class FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1024): self.dim = dim
    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Deterministic fake: hash the text into a vector.
        import hashlib
        return [
            [((int(hashlib.sha256(t.encode()).hexdigest(), 16) >> i) & 0xFF) / 255.0
             for i in range(self.dim)]
            for t in texts
        ]

class FakeChatProvider(ChatProvider):
    def __init__(self, text: str = "fake answer", model: str = "fake-model"):
        self._text, self._model = text, model
    async def chat(self, *, system: str, user: str, temperature=0.2, model=None) -> tuple[str, ChatUsage]:
        return self._text, ChatUsage(prompt_tokens=len(system) + len(user),
                                     completion_tokens=len(self._text), model=model or self._model)
```

Real network calls to Mistral / NVIDIA NIM belong in **adapter smoke tests** under `tests/unit/infrastructure/ai/`, gated by env vars and skipped in default CI runs.

## Step 8: What CI should run

**See the shipped workflow:** [`.github/workflows/test.yml`](../../.github/workflows/test.yml) — one-line summary: the `backend` job applies every `/db/migrations/*.sql` to the `services.db` container for the role-audit step, then runs `uv run pytest -v` (which spins up its own `pgvector/pgvector:pg18` testcontainer with the real `rw_app_login` and exercises the BDD); the `frontend` job runs `npm install && npm run build`.

CI must have Docker available (testcontainers requires it). On hosts without Docker, run `pytest -q tests/unit --ignore=tests/features` — the unit-only subset skips the container fixture.

## Step 9: Project-banned patterns (test-specific list)

| Banned | Use instead |
|---|---|
| Granting `BYPASSRLS` to the test role "to make setup easier" | Set `app.current_user_id` per actor via the `as_actor` fixture; the test then exercises real RLS |
| Mocking the database with `unittest.mock` | testcontainers + real `pgvector` extension |
| Asserting on the SQL query text (`"SELECT ... FROM rw_visible_message"`) | Assert on the response shape and contents — the SQL is an implementation detail |
| Sharing state between scenarios via module-level globals | Use a `ctx` fixture (function scope) |
| `time.sleep(...)` to wait for async background work | Poll the DB or the API until the expected state arrives |
| Hitting real Mistral / NVIDIA endpoints in unit tests | `FakeChatProvider` / `FakeEmbeddingProvider` |
| Recording real API keys in fixtures | `os.environ["MISTRAL_API_KEY"]` only if the smoke test is explicitly enabled |
| Using `pytest --forked` or `pytest-xdist` without thinking about container cleanup | Default to sequential; if you parallelize, scope the container per *worker*, not per session |
| Hitting `/auth/login` to mint a JWT in every step | Mint tokens directly in the seed helper (`ctx.tokens[name] = ...`) — auth is tested in its own feature file |
| Building the DB schema from scratch in every test (`CREATE TABLE ...` inside the test) | Migrations run once in the session fixture |

## Step 10: Where to go next

- For the SQL/RLS work the tests assert against, use the `postgresql-rls-pgvector` skill.
- For the FastAPI use cases the tests exercise, use the `fastapi-development` skill.
- For the AI provider fakes and the denial taxonomy, use the `ai-provider-integration` skill.
- For architectural questions, the source of truth is `/docs/ARCHITECTURE.md`. If this skill and the architecture disagree, **the architecture wins**.