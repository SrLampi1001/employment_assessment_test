---
name: pytest-bdd-testcontainers
description: Write, run, and debug Behaviour-Driven Development tests for the Riwi Co. Messaging Platform backend using pytest, pytest-bdd (Gherkin feature files), and testcontainers-python against a real `pgvector/pgvector:pg18` PostgreSQL instance. Trigger for any work under /backend/tests/, for any new feature file (.feature), step definition, scenario, or fixture, and for the two mandatory BDD scenarios that prove RLS works (visible-messages-by-membership + author-always-sees-own). Use for container lifecycle (Postgres, Redis, Mistral emulator), fixture composition, Gherkin best practices, `app.current_user_id` per-actor setup, and CI gating. Do NOT use for plain unit tests on pure Python (use the fastapi-development skill), for raw SQL/RLS work (use postgresql-rls-pgvector), or for AI provider behavior tests (use ai-provider-integration).
---

# pytest + pytest-bdd + testcontainers — Riwi Co. Messaging Platform

## Ground rule: the security tests must exercise the real RLS policy

Per [`/docs/ARCHITECTURE.md §10`](../../../docs/ARCHITECTURE.md), the two mandatory scenarios are *executable specifications* of the security model. They MUST run against a **real PostgreSQL** (via testcontainers), with the **real `pgvector` extension**, executing as the **`rw_app_login` role** (which inherits `rw_app`, NOLOGIN, no `BYPASSRLS`). Mocking the DB, granting `BYPASSRLS`, or overriding RLS in a test fixture defeats the point of the test — the whole guarantee is that the same policy applies to the backend, the seed script, and `psql` as a DBA.

If a test needs data the RLS policy would hide, set `app.current_user_id` to the actor that *should* see it. Do not bypass RLS.

## Project baseline

| Item | Pin | Why |
|---|---|---|
| pytest | **9.x** | Current stable |
| pytest-bdd | latest | Gherkin feature files in `.feature` |
| httpx | latest | FastAPI `TestClient` backend |
| testcontainers-python | **4.x** | Real services in Docker, scoped per test session |
| Postgres image | **`pgvector/pgvector:pg18`** (pinned in the fixture) | Matches `ARCHITECTURE.md §11` |
| Docker | required for local runs; CI provides it | testcontainers shells out to `docker` |
| Runtime test role | `rw_app_login` (no `BYPASSRLS`); `rw_app` (NOLOGIN, no `BYPASSRLS`, no `SUPERUSER`); `rw_migrator` (DDL only) | Same roles as production |
| Test runner | `backend/.venv/bin/python -m pytest -q tests/` | One command; no Makefile dance |

Layout (per `ARCHITECTURE.md §11` — verified 2026-08-29):

```
backend/tests/
├── conftest.py                 — testcontainers fixture, app fixture, settings override, _seed TRUNCATE
├── test_smoke.py               — minimal import + FastAPI app-build smoke
├── fake_chat_provider.py       — FakeEmbeddingProvider + FakeChatProvider (single source)
├── features/
│   ├── membership.feature       — the two mandatory BDD scenarios (ARCH §10)
│   ├── auth.feature             — register / login / refresh rotation / JWT middleware
│   ├── channels.feature         — create / invite / leave / list
│   ├── messages.feature         — send / edit / delete / search / non-author 404
│   ├── search.feature           — ts_headline + highlight
│   ├── copilot.feature          — ask + citation + denial taxonomy
│   └── rls_isolation.feature    — RLS enforcement on rw_copilot_usage + rw_refresh_token
├── step_defs/                   — one .py per .feature (test_<name>.py)
│   ├── test_membership.py
│   ├── test_auth.py
│   ├── test_channels.py
│   ├── test_messages.py
│   ├── test_search.py
│   ├── test_copilot.py
│   └── test_rls_isolation.py
├── infrastructure/ai/
│   └── test_smoke.py            — gated by RUN_AI_SMOKE=1; live Mistral + NVIDIA calls
└── unit/                        — plain pytest functions, no feature file
    ├── application/{auth,channels,copilot,messages}/test_*.py
    └── scripts/test_seed.py
```

## Step 1: The session-scoped Postgres fixture

The Postgres container is started **once per test session** — not once per test — because spinning up a container takes 2–5 seconds and the migrations are expensive. Tests share the database but each test gets a clean dataset via `_seed` (autouse TRUNCATE).

**See the shipped fixture:** [`/backend/tests/conftest.py`](../../../backend/tests/conftest.py) — one-line summary: `pg_container` (session-scoped `PostgresContainer("pgvector/pgvector:pg18")`), `_bootstrap` (applies migrations + creates `rw_app_login` with the test password — **`_apply_migrations` deliberately skips `0002_roles.sql`** because `_create_runtime_role` builds the roles with a test-only password), `pg_super_url` / `pg_app_url` (the two URLs the rest of the suite uses), `super_conn` (superuser — setup-only), `actor_conn` (`rw_app_login` — every read-and-assert goes here), and the autouse `_seed` fixture that TRUNCATEs the dataset before each test so tests are independent. The canonical Valentina / Camila UUIDs are exported from `conftest` so step defs and the feature file stay in sync.

Per-test rollback vs truncate lives in the `references/per-test-rollback.md` (the project uses TRUNCATE, not transactional rollback, because the refresh-token-rotation tests need to *see* the commit from a follow-up request).

## Step 2: The "act as" fixture (the RLS safety net)

Every test that touches business data must set `app.current_user_id` to the actor it's testing as. In production this is done by `RwSession` (transaction-local `SET LOCAL`). In tests, the same thing happens because every request goes through `create_app` → `JwtAuthMiddleware` → `RwSession`, mirroring production exactly. **Never connect as a `postgres` superuser for the actual query under test** — superusers bypass RLS by definition.

**See the shipped helper:** [`/backend/tests/conftest.py`](../../../backend/tests/conftest.py) — the `super_conn` fixture (superuser, setup-only) and the `actor_conn` fixture (`rw_app_login`, every read-and-assert). The autouse `_seed` fixture resets the dataset between tests via `TRUNCATE rw_message, rw_message_edit, rw_message_read, rw_channel_member, rw_channel, rw_user, rw_copilot_usage, rw_refresh_token CASCADE` — the last two are included so the RLS-isolation tests stay green.

The test asserts the security guarantee, not the implementation. If you find yourself asserting on the absence of an `EXISTS` clause or a specific GUC name, you're testing the wrong layer.

## Step 3: The FastAPI app fixture

**See the shipped fixture:** [`/backend/tests/conftest.py:http_client`](../../../backend/tests/conftest.py) — one-line summary: builds `create_app(settings=..., session_factory=pg_app_session_factory, embedder=FakeEmbeddingProvider(), chatter=FakeChatProvider(use_shared=True))` and wraps it in `fastapi.testclient.TestClient`. The `embedder` and `chatter` are injection seams — `FakeEmbeddingProvider` and `FakeChatProvider` live in `/backend/tests/fake_chat_provider.py`. Tests use `httpx.Client` (sync) backed by Starlette's `TestClient` (in-process, no real network). Routes connect as `rw_app_login` so RLS is in force end-to-end.

The `create_app` factory takes `embedder` / `chatter` as parameters — there is **no DI container** (`dishka` / `punq` / `dependency-injector` would be the wrong tool here; see `fastapi-development` / Step 4.8). Don't add one in a PR.

## Step 4: The two mandatory BDD scenarios

These come straight from `ARCHITECTURE.md §10`. They are the executable spec for the security model — keep them green.

**See the shipped feature file:** [`/backend/tests/features/membership.feature`](../../../backend/tests/features/membership.feature) — one-line summary: two scenarios (non-member cannot see private channel's messages across history / search / copilot; author always sees their own messages despite role changes) wired to the `_seed` fixture in `conftest.py`. Step definitions live in [`/backend/tests/step_defs/test_membership.py`](../../../backend/tests/step_defs/test_membership.py) and are bound to the feature via `scenarios(str(_FEATURE_FILE))` (pytest-bdd 8.x removed auto-discovery).

Notes on the Gherkin:

- **Plain English, not implementation.** No `app.current_user_id`, no `rw_channel_member`, no SQL — the scenario should read like a product spec.
- **Concrete actors, not "user #1".** Names make test failures diagnostic.
- **Each `When`/`Then` pair is one assertion.** Don't pack 4 expectations into a single `Then`.
- **The copilot step uses the denial taxonomy** from `ai-provider-integration/references/denial-taxonomy.md`. If the wording drifts, both the prompt and the test must change together.

## Step 5: Step definitions (skeleton)

**See the shipped step defs:** [`/backend/tests/step_defs/test_membership.py`](../../../backend/tests/step_defs/test_membership.py) — one-line summary: the canonical mapping between the Gherkin `Given/When/Then` and the SQL + RLS assertions. Copilot-specific step defs (including the `infer:low-confidence` scenario) live in the same file so the BDD stays next to the seed fixture and the dataset and assertions cannot drift.

> **pytest-bdd cross-module step lookup gotcha:** when a new feature file imports a small handful of Given/When/Then steps, define them locally in the step-defs module. pytest-bdd's cross-module step lookup can race with import order — duplicating locally (as `test_rls_isolation.py` does for its handful of login/register/channel/send-message/ask-copilot/fetch-usage steps) keeps the file self-contained.

## Step 6: Per-test isolation (rollback vs truncate)

Two patterns, pick one per test layer:

| Pattern | When to use | Trade-off |
|---|---|---|
| Transactional rollback (`BEGIN; ...; ROLLBACK;`) | Fast unit/BDD tests; no DDL inside the test | All test data must fit in one transaction; can't test commits |
| `TRUNCATE rw_message, rw_channel, ... RESTART IDENTITY CASCADE` | Tests that exercise commit semantics or COMMIT-after-INSERT flows | Slower; test order can leak if you forget a table |

For the two mandatory security scenarios, transactional rollback is enough — you're testing *visibility*, not commit semantics. For the auth flow (refresh-token rotation commits), use truncate. **The project uses TRUNCATE-only** (`_seed` autouse fixture in `conftest.py`), because the refresh-token-rotation tests need to *see* the committed row from a follow-up request.

For the gotchas around `set_config(name, value, true)` being transaction-local, see `references/per-test-rollback.md`.

## Step 7: Faking the AI providers

**See the shipped fakes:** [`/backend/tests/fake_chat_provider.py`](../../../backend/tests/fake_chat_provider.py) — one-line summary: `FakeEmbeddingProvider` (deterministic hash-based 1024-dim vectors) + `FakeChatProvider` (canned text + optional shared `_SHARED_RESPONSE` state for the BDD pushback scenario). The shared state is reset by the `_reset_default()` autouse fixture in `conftest.py` so pushback scenarios don't leak across tests.

Real network calls to Mistral / NVIDIA NIM belong in **adapter smoke tests** under `backend/tests/infrastructure/ai/`, gated by env vars (`RUN_AI_SMOKE=1`) and skipped in default CI runs. The agent must NEVER run these tests automatically — they require real API keys in `.env`.

## Step 8: What CI should run

**See the shipped workflow:** [`.github/workflows/test.yml`](../../../.github/workflows/test.yml) — one-line summary: the `backend` job applies every `/db/migrations/*.sql` to the `services.db` container for the role-audit step, then runs `uv run pytest -v` (which spins up its own `pgvector/pgvector:pg18` testcontainer with the real `rw_app_login` and exercises the BDD); the `frontend` job runs `npm install && npm run build`.

CI must have Docker available (testcontainers requires it). On hosts without Docker, run `pytest -q tests/unit --ignore=tests/features` — the unit-only subset skips the container fixture.

## Step 9: Project-banned patterns (test-specific list)

| Banned | Use instead |
|---|---|
| Granting `BYPASSRLS` to the test role "to make setup easier" | Set `app.current_user_id` per actor via `as_actor` fixture (or use `actor_conn` + the application layer); the test then exercises real RLS |
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