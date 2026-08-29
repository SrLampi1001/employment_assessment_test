"""BDD test fixtures: pgvector testcontainer + RLS-aware connection.

See ARCHITECTURE.md §10 + .agents/skills/pytest-bdd-testcontainers (Step 11)
for the design contract:

- A real `pgvector/pgvector:pg18` testcontainer is spun up (no mocking RLS).
- All migrations under /db/migrations/ are applied once at session scope.
- The runtime role (`rw_app_login`, inherits `rw_app` NOLOGIN no BYPASSRLS)
  is created in the testcontainer; tests connect through it.
- `as_actor(cur, user_id)` sets the transaction-local GUC so RLS policies
  can read the actor identity.

The key invariant: tests never connect as the migrator / superuser
except for one-time setup. Every read-and-assert goes through
`rw_app_login` so the RLS policies are the only filter.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer


# Hard-coded identifiers so the feature file (Given "Valentina", "Camila")
# maps to a stable dataset across runs without dragging a seed file into
# Phase 1. Phase 7's seed loader replaces this fixture with the real corpus.
VALENTINA = uuid.UUID("11111111-1111-1111-1111-111111111111")
CAMILA = uuid.UUID("22222222-2222-2222-2222-222222222222")
CHANNEL_PRIVATE = uuid.UUID("aaaaaaaa-1111-1111-1111-111111111111")
CHANNEL_TEAM1 = uuid.UUID("bbbbbbbb-1111-1111-1111-111111111111")

# Test role password — local-only, never real.
APP_PASSWORD = "test_app_password"

# Path to the migrations directory, relative to this file.
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def _apply_migrations(super_conn: psycopg.Connection) -> None:
    """Apply the schema migrations in lexicographic order.

    Two docker-init files are deliberately handled here:
      - 0001_extensions.sql is applied explicitly (the pgvector testcontainer
        image ships the extension but it is not enabled by default);
      - 0002_roles.sql is SKIPPED — `_create_runtime_role(...)` below
        creates rw_app / rw_app_login with the test password; re-applying
        0002 would collide with that password.

    The remaining migrations use `IF NOT EXISTS` / `CREATE OR REPLACE`
    / `DROP IF EXISTS` patterns so re-running against an already-migrated
    DB is a no-op.
    """
    for sql_file in sorted(MIGRATIONS_DIR.glob("0[0-9][0-9][0-9]_*.sql")):
        if sql_file.name == "0002_roles.sql":
            continue
        with sql_file.open() as fh:
            super_conn.execute(fh.read())
    super_conn.commit()


def _create_runtime_role(super_conn: psycopg.Connection) -> None:
    """Create rw_app (NOLOGIN) + rw_app_login (LOGIN IN ROLE rw_app).

    These mirror the production roles from /db/migrations/0002_roles.sql.
    In testcontainers there is no init script that creates them, so we
    set them up here as part of the one-time session fixture.
    """
    super_conn.execute("DROP ROLE IF EXISTS rw_app_login")
    super_conn.execute("DROP ROLE IF EXISTS rw_app")
    super_conn.execute("CREATE ROLE rw_app NOLOGIN")
    super_conn.execute(
        f"CREATE ROLE rw_app_login WITH LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE rw_app"
    )
    super_conn.commit()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """Real pgvector container, started once per test session."""
    with PostgresContainer("pgvector/pgvector:pg18") as pg:
        yield pg


def _psycopg_url(raw_url: str) -> str:
    """Convert a SQLAlchemy-style `postgresql+psycopg://...` URL to the
    plain `postgresql://...` form that psycopg 3 accepts.
    """
    if "+psycopg" in raw_url:
        return raw_url.replace("postgresql+psycopg", "postgresql", 1)
    return raw_url


@pytest.fixture(scope="session")
def pg_super_url(pg_container: PostgresContainer) -> str:
    """Superuser connection URL — for migrations + role setup only."""
    return _psycopg_url(pg_container.get_connection_url(driver="psycopg"))


@pytest.fixture(scope="session")
def pg_app_url(pg_container: PostgresContainer) -> str:
    """rw_app_login connection URL — for every read-and-assert.

    Replaces the user/password in the superuser URL so tests connect as
    the runtime role, with the same posture as the production application.
    """
    url = _psycopg_url(pg_container.get_connection_url(driver="psycopg"))
    if "@" in url:
        head, tail = url.split("@", 1)
        scheme_user, _password = head.split(":", 1)
        scheme = scheme_user.split("://", 1)[0]
        return f"{scheme}://rw_app_login:{APP_PASSWORD}@{tail}"
    raise RuntimeError(f"Unexpected connection URL: {url}")


@pytest.fixture(scope="session", autouse=True)
def _bootstrap(pg_super_url: str) -> Iterator[None]:
    """Apply migrations + create the runtime role before any test runs."""
    with psycopg.connect(pg_super_url, autocommit=False) as conn:
        _create_runtime_role(conn)
        _apply_migrations(conn)
    yield


@pytest.fixture
def super_conn(pg_super_url: str) -> Iterator[psycopg.Connection]:
    """One connection per test, superuser — for SETUP ONLY (no reads)."""
    with psycopg.connect(pg_super_url, autocommit=False) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def actor_conn(pg_app_url: str) -> Iterator[psycopg.Connection]:
    """One connection per test, rw_app_login — every read-and-assert goes here.

    RLS is active (no BYPASSRLS), so the connection sees only rows
    permitted by the actor GUC set in `as_actor(...)`.
    """
    with psycopg.connect(pg_app_url, autocommit=False) as conn:
        yield conn
        conn.rollback()


def as_actor(conn: psycopg.Connection, actor_id: uuid.UUID | None) -> None:
    """Set the actor GUC for this transaction.

    Pass `None` to unset (so the no-actor control scenario can verify the
    fail-closed behavior of the policies).
    """
    if actor_id is None:
        conn.execute("SELECT set_config('app.current_user_id', NULL, false)")
    else:
        conn.execute(
            "SELECT set_config('app.current_user_id', %s, false)",
            (str(actor_id),),
        )


@pytest.fixture
def pg_app_session_factory(pg_app_url: str):
    """Session factory that yields a fresh `rw_app_login` connection.

    Used by the FastAPI app's RwSession in the BDD auth tests.
    Phase 7 replaces this with a real psycopg_pool.AsyncConnectionPool;
    Phase 2 uses one connection per request for simplicity.
    """
    from collections.abc import Callable

    from psycopg import Connection

    def factory() -> Connection:
        return Connection.connect(pg_app_url, autocommit=False)

    return factory


@pytest.fixture
def http_client(pg_app_session_factory, pg_app_url: str):
    """Build a FastAPI app wired to the testcontainer, return an
    httpx.Client (sync) backed by FastAPI's TestClient (no real network).

    TestClient wraps ASGITransport under the hood and exposes a
    synchronous `.get()` / `.post()` API — exactly what the sync
    BDD step definitions need. Routes connect as `rw_app_login`
    so RLS is in force end-to-end (the auth tests prove the actor
    GUC is set on every request).
    """
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from tests.fake_chat_provider import FakeChatProvider, FakeEmbeddingProvider

    settings = Settings(
        jwt_secret="test-jwt-secret-with-multiple-characters",
        access_ttl_seconds=900,
        refresh_ttl_seconds=3600,
        database_url=pg_app_url,
        # CORS is irrelevant for in-process TestClient calls (no
        # real origin), so the allow-list is empty — `create_app`
        # reads `settings.cors_origins` directly and the
        # CORSMiddleware no-ops when the list is empty.
        cors_origins=[],
        # Phase 6: AI providers — empty strings in tests so the
        # main factory falls back to the "ai-not-configured" stubs.
        # We override the embedder + chatter below with fakes so
        # the copilot BDD scenarios can exercise RLS gating +
        # the denial-taxonomy classification without live API
        # calls (per ai-provider-integration / Step 9: adapter
        # smoke tests are gated by RUN_AI_SMOKE=1).
        mistral_api_key="",
        nvidia_api_key="",
        mistral_embed_model="mistral-embed",
        mistral_embed_dim=1024,
        chat_model_primary="mistralai/mistral-nemotron",
        chat_model_fallback="nvidia/nemotron-3.5-lightning-30b-a3b",
        chat_temperature=0.2,
        chat_request_timeout_s=30.0,
    )
    app = create_app(
        settings=settings,
        session_factory=pg_app_session_factory,
        embedder=FakeEmbeddingProvider(),
        # `use_shared=True` makes the chatter honor the BDD shared
        # `_SHARED_RESPONSE` state that `set_response()` /
        # `push_back` mutate mid-scenario (see tests/step_defs/
        # test_copilot.py for the safe-comply Scenario C). Default
        # instances use their own `_response` attribute and would
        # ignore the BDD state setter — making the pushback scenario
        # impossible.
        chatter=FakeChatProvider(use_shared=True),
    )
    return TestClient(app)


@pytest.fixture
def ctx() -> dict:
    """Mutable per-scenario state shared across BDD step definitions.

    Lives in `conftest.py` because pytest-bdd's auto-fixture lookup
    sees module-level fixtures only when they're in a conftest or in
    the same module as the scenario. Step files in `tests/step_defs/`
    then pull `ctx` as a parameter. See `tests/step_defs/test_auth.py`
    and `tests/step_defs/test_channels.py` for the consumers.
    """
    return {
        "users": {},          # username -> password
        "tokens": {},         # username -> {access, refresh}
        "channels": {},       # channel_name -> channel_id
        "first_refresh": {},  # username -> original refresh token
        "registered": {},     # username -> password (also in users)
        "last_response": None,
    }


@pytest.fixture(autouse=True)
def _reset_fake_chat_state():
    """Reset the fake chat provider's `_next_response` to the default
    deny:insufficient-context before each test, so pushback BDD
    scenarios don't leak state. The fake lives in
    `tests/fake_chat_provider.py`; this autouse fixture lives in
    conftest so it's honoured regardless of which test module runs.
    """
    from tests.fake_chat_provider import _reset_default

    _reset_default()
    yield
    _reset_default()


@pytest.fixture(autouse=True)
def _seed(super_conn: psycopg.Connection) -> None:
    """Seed the two BDD scenarios with their canonical dataset.

    Runs before each test; the cascade TRUNCATE keeps tests independent.
    The dataset matches the Gherkin: Valentina (not in Camila-private),
    Camila (owner of both), one message per scenario.
    """
    with super_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE rw_message, rw_message_edit, rw_message_read, "
            "rw_channel_member, rw_channel, rw_user, "
            "rw_copilot_usage, rw_refresh_token CASCADE"
        )
        cur.executemany(
            "INSERT INTO rw_user (rw_id, rw_username, rw_display_name, rw_locale) "
            "VALUES (%s, %s, %s, %s)",
            [
                (VALENTINA, "valentina", "Valentina", "es"),
                (CAMILA, "camila", "Camila", "es"),
            ],
        )
        cur.executemany(
            "INSERT INTO rw_channel (rw_id, rw_name, rw_kind, rw_created_by) "
            "VALUES (%s, %s, %s, %s)",
            [
                (CHANNEL_PRIVATE, "Camila-private", 1, CAMILA),
                (CHANNEL_TEAM1, "team-1", 2, CAMILA),
            ],
        )
        cur.executemany(
            "INSERT INTO rw_channel_member (rw_channel_id, rw_user_id, rw_role) "
            "VALUES (%s, %s, %s)",
            [
                (CHANNEL_PRIVATE, CAMILA, 2),       # Camila = owner
                (CHANNEL_TEAM1, CAMILA, 2),         # Camila = owner
                (CHANNEL_TEAM1, VALENTINA, 1),      # Valentina = member
            ],
        )
        cur.executemany(
            "INSERT INTO rw_message (rw_channel_id, rw_author_id, rw_body) "
            "VALUES (%s, %s, %s)",
            [
                # Scenario A: a private message Valentina must NOT see.
                (CHANNEL_PRIVATE, CAMILA,
                 "Este es un mensaje privado de Camila"),
                # Scenario B: Valentina's own message in team-1.
                (CHANNEL_TEAM1, VALENTINA,
                 "Hola equipo, soy Valentina"),
            ],
        )
    super_conn.commit()
