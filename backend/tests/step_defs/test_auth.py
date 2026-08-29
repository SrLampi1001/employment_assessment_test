"""Step definitions for the auth BDD feature.

The scenarios exercise the **full HTTP stack** against the FastAPI app
backed by the pgvector testcontainer. Each scenario runs as
`rw_app_login` (no BYPASSRLS) so the actor GUC is set on every
transaction the middleware + RwSession open.

For Phase 2 there is no /api/v1/me yet — the "protected endpoint"
scenarios use a tiny `GET /api/v1/me` shim added by this test step
file: it just returns 200 + the actor_id if the middleware accepted
the token, 401 otherwise. The shim lives here (not in delivery.py)
because it is a test-only convenience; the real /api/v1/me lands in
Phase 3 (Profile).
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import jwt
import pytest
from pytest_bdd import given, parsers, scenarios, then, when
import httpx

# Wire the scenarios from auth.feature.
_FEATURE_FILE = Path(__file__).resolve().parents[1] / "features" / "auth.feature"
scenarios(str(_FEATURE_FILE))


# ─── Fixtures & state ───────────────────────────────────────────────────


@pytest.fixture
def ctx() -> dict:
    """Mutable per-scenario state shared across step definitions."""
    return {
        "registered": {},     # username -> password
        "tokens": {},         # username -> {access, refresh}
        "first_refresh": {},  # username -> original refresh token plaintext
    }


# ─── Given steps ────────────────────────────────────────────────────────


@given(parsers.cfparse("a user with username {username} and password {password}"))
def register_via_http(
    username: str,
    password: str,
    ctx: dict,
    http_client: httpx.Client,
):
    resp = http_client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": username.title(),
            "locale": "es",
            "password": password,
        },
    )
    assert resp.status_code == 201, (
        f"register failed: status={resp.status_code} body={resp.text}"
    )
    ctx["registered"][username] = password


@given(parsers.cfparse("a user with username {username} and password {password} exists"))
def register_existing_user(
    username: str,
    password: str,
    ctx: dict,
    http_client: httpx.Client,
):
    register_via_http(username, password, ctx, http_client)


@given(parsers.cfparse("the user has logged in with username {username} and password {password}"))
def login_and_capture_tokens(
    username: str,
    password: str,
    ctx: dict,
    http_client: httpx.Client,
):
    resp = http_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ctx["tokens"][username] = {
        "access": body["access_token"],
        "refresh": body["refresh_token"],
    }


@given("the user has rotated the refresh token once")
def rotate_once(
    ctx: dict,
    http_client: httpx.Client,
):
    # Use whichever user has tokens in the scenario.
    username = next(iter(ctx["tokens"]))
    first_refresh = ctx["tokens"][username]["refresh"]
    ctx["first_refresh"][username] = first_refresh
    resp = http_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ctx["tokens"][username]["access"] = body["access_token"]
    ctx["tokens"][username]["refresh"] = body["refresh_token"]


# ─── When steps ─────────────────────────────────────────────────────────


@when("the user registers")
def register_step(http_client: httpx.Client, ctx: dict):
    username = next(iter(ctx["registered"]))
    password = ctx["registered"][username]
    resp = http_client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": username.title(),
            "locale": "es",
            "password": password,
        },
    )
    ctx["last_response"] = resp


@when(
    parsers.cfparse("the user logs in with username {username} and password {password}")
)
def login_step(username: str, password: str, http_client: httpx.Client, ctx: dict):
    resp = http_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    ctx["last_response"] = resp
    if resp.status_code == 200:
        body = resp.json()
        ctx["tokens"][username] = {
            "access": body["access_token"],
            "refresh": body["refresh_token"],
        }


@when(parsers.cfparse("the user registers with username {username} and password {password}"))
def register_step(
    username: str,
    password: str,
    http_client: httpx.Client,
    ctx: dict,
):
    resp = http_client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": username.title(),
            "locale": "es",
            "password": password,
        },
    )
    ctx["last_response"] = resp
    if resp.status_code == 201:
        ctx["registered"][username] = password


@when("the user refreshes with the valid refresh token")
def refresh_step(http_client: httpx.Client, ctx: dict):
    username = next(iter(ctx["tokens"]))
    resp = http_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": ctx["tokens"][username]["refresh"]},
    )
    ctx["last_response"] = resp


@when(
    "the user attempts to refresh again with the FIRST refresh token"
)
def refresh_reuse_step(http_client: httpx.Client, ctx: dict):
    username = next(iter(ctx["first_refresh"]))
    resp = http_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": ctx["first_refresh"][username]},
    )
    ctx["last_response"] = resp


@when("the user accesses a protected endpoint with the access token")
def protected_with_token(http_client: httpx.Client, ctx: dict):
    username = next(iter(ctx["tokens"]))
    resp = http_client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {ctx['tokens'][username]['access']}"},
    )
    ctx["last_response"] = resp


@when("the user accesses a protected endpoint without an Authorization header")
def protected_without_token(http_client: httpx.Client, ctx: dict):
    resp = http_client.get("/api/v1/me")
    ctx["last_response"] = resp


@when("the user accesses a protected endpoint with an expired access token")
def protected_with_expired_token(http_client: httpx.Client, ctx: dict):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "iat": int(past.timestamp()),
        "exp": int(past.timestamp()),
        "type": "access",
    }
    token = jwt.encode(payload, "test-jwt-secret-with-multiple-characters", algorithm="HS256")
    resp = http_client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    ctx["last_response"] = resp


@when("the user accesses a protected endpoint with a tampered token")
def protected_with_tampered_token(http_client: httpx.Client, ctx: dict):
    # Sign a token then flip the last character → signature is now
    # invalid; the middleware must reject it.
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        "type": "access",
    }
    token = jwt.encode(payload, "test-jwt-secret-with-multiple-characters", algorithm="HS256")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    resp = http_client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {tampered}"},
    )
    ctx["last_response"] = resp


# ─── Then steps ─────────────────────────────────────────────────────────


@then(parsers.cfparse("the response status is {code:d}"))
def assert_status(code: int, ctx: dict):
    actual = ctx["last_response"].status_code
    assert actual == code, (
        f"expected {code}, got {actual}; body={ctx['last_response'].text}"
    )


@then("the user exists in the database")
def assert_user_in_db(ctx: dict, super_conn):
    username = next(iter(ctx["registered"]))
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_id FROM rw_user WHERE rw_username = %s", (username,)
        )
        assert cur.fetchone() is not None, f"user {username!r} not in rw_user"


@then("the response contains an access token")
def assert_access_token(ctx: dict):
    assert "access_token" in ctx["last_response"].json()


@then("the response contains a refresh token")
def assert_refresh_token(ctx: dict):
    assert "refresh_token" in ctx["last_response"].json()


@then("the response contains a new refresh token")
def assert_new_refresh_token(ctx: dict):
    body = ctx["last_response"].json()
    username = next(iter(ctx["tokens"]))
    assert body["refresh_token"] != ctx["tokens"][username]["refresh"], (
        "rotation must produce a new refresh token"
    )


@then("the old refresh token is revoked in the database")
def assert_old_revoked(ctx: dict, super_conn):
    username = next(iter(ctx["tokens"]))
    # The original refresh token is captured in first_refresh[username]
    # when the rotate-once step ran. If that step wasn't used (rotation
    # was the first action), fall back to the captured tokens before
    # the refresh response was processed.
    original = ctx["first_refresh"].get(username) or ctx["tokens"][username]["refresh"]
    token_hash = hashlib.sha256(original.encode()).hexdigest()
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_revoked_at FROM rw_refresh_token WHERE rw_token_hash = %s",
            (token_hash,),
        )
        row = cur.fetchone()
        assert row is not None, "refresh row missing"
        assert row[0] is not None, "old refresh token is not revoked"


@then("every refresh token in the family is revoked in the database")
def assert_family_revoked(ctx: dict, super_conn):
    username = next(iter(ctx["first_refresh"]))
    family_token = ctx["first_refresh"][username]
    token_hash = hashlib.sha256(family_token.encode()).hexdigest()
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_family_id FROM rw_refresh_token WHERE rw_token_hash = %s",
            (token_hash,),
        )
        family_id = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM rw_refresh_token "
            "WHERE rw_family_id = %s AND rw_revoked_at IS NULL",
            (family_id,),
        )
        unrevoked = cur.fetchone()[0]
        assert unrevoked == 0, (
            f"family {family_id} has {unrevoked} unrevoked tokens after reuse "
            "detection — reuse-detection revoked only the replayed row, not the family"
        )
