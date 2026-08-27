# Per-Test Rollback vs TRUNCATE

The cleanest way to keep BDD tests fast *and* isolated against a real PostgreSQL is per-test transactional rollback. Use this for everything that doesn't need to see the effect of a `COMMIT` inside the test itself.

## Pattern: function-scoped connection, ROLLBACK on teardown

```python
# /backend/tests/conftest.py
import pytest
import psycopg

@pytest.fixture
def db(pg_url):
    """
    Yields a connection wrapped in a transaction. On teardown, ROLLBACK
    wipes everything the test did — including any seeds, RLS GUC changes,
    and temp tables. The next test starts clean.
    """
    conn = psycopg.connect(pg_url, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
```

### Pros

- **Fast**: no `TRUNCATE` round-trips; the rollback just unwinds the transaction.
- **Comprehensive**: undoes *everything* done in the connection — even things `TRUNCATE` doesn't touch (sequence state, temp tables, session GUCs like `app.current_user_id`).

### Cons / limitations

- **Single connection per test.** If the test (or the code under test) opens a second connection, it won't see the uncommitted state. The fix is to keep the production code path using *one* `psycopg_pool.AsyncConnectionPool` per request — which is what the architecture mandates anyway.
- **Cannot test commit semantics.** The auth flow (rotate refresh token → commit) needs to *see* the committed row from the next request. For those, switch to the truncate pattern.

## Pattern: TRUNCATE between tests

For tests that exercise commit boundaries:

```python
@pytest.fixture
def db_clean(pg_url):
    conn = psycopg.connect(pg_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE
                    rw_copilot_usage, rw_message_read, rw_message_edit,
                    rw_message, rw_channel_member, rw_channel,
                    rw_refresh_token, rw_auth_credential, rw_user
                RESTART IDENTITY CASCADE;
            """)
        yield conn
    finally:
        conn.close()
```

Use this for:

- Refresh-token rotation tests (the second request needs to *see* the committed revocation).
- Concurrent-write race tests (need committed rows on the second connection).
- Anything where the code under test commits and then a follow-up request reads.

## Combining both

A common layout: the session fixture sets up schema + role; the function fixture provides `db` (transactional) by default; tests that need commit semantics request `db_clean` explicitly.

```python
def test_message_send(db, ctx):       # transactional — fast
    ...

def test_refresh_token_rotation(db_clean, ctx):  # truncate — slower, sees commits
    ...
```

## One real subtlety: `set_config(name, value, true)` is transaction-local

The `true` third argument to `SELECT set_config(...)` is what makes the actor GUC reset on `COMMIT` / `ROLLBACK`. So per-test transactional rollback also wipes `app.current_user_id` — no need to `RESET` it manually.

If you ever switch the GUC to session-level (`false`), you'd need to either `RESET app.current_user_id` in the fixture or use the truncate pattern.

## Docker / testcontainers teardown

`PostgresContainer` is a context manager — leaving it open across all tests in the session is correct, and `with PostgresContainer(...) as pg:` in the session fixture handles teardown automatically. Do **not** wrap the container in a function-scoped fixture; container startup takes seconds and dominates test time if you do.