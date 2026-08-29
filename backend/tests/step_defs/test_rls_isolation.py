"""Step definitions for the RLS isolation feature.

Per ARCHITECTURE.md §3 + issue #22: every rw_* table that carries
per-user state must reject any cross-user access — even from the
runtime role. These tests assert the two tables that were hardened
in migration 0140:

1. `rw_copilot_usage`: Alice's audit rows must be invisible to Bob.
2. `rw_refresh_token`: the runtime role's direct table privileges
   were REVOKEd; only the SECURITY DEFINER functions are accessible.

The cross-module Given/When steps are duplicated locally to keep the
file self-contained — pytest-bdd's step registry is populated at
import-time, and explicit local copies avoid the cross-module step
lookup races that other BDD suites in this repo work around by
duplicating the steps (see `test_copilot.py` vs `test_messages.py`
for the same pattern).
"""
from __future__ import annotations

from pathlib import Path

import httpx
from pytest_bdd import given, parsers, scenarios, then, when

_FEATURE_FILE = (
    Path(__file__).resolve().parents[1] / "features" / "rls_isolation.feature"
)

scenarios(str(_FEATURE_FILE))


# ─── Shared Given/When steps (duplicate of test_messages / test_copilot)
# pytest-bdd's global step registry resolves steps by string match; a
# locally-defined step shadows the cross-module one for these scenarios.


@given(parsers.cfparse("a user with username {username} and password {password} exists"))
def user_exists(username, password, ctx, http_client):
    resp = http_client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": username.title(),
            "locale": "es",
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    ctx["users"][username] = password


@given(parsers.cfparse("the user has logged in with username {username} and password {password}"))
def login_step(username, password, ctx, http_client):
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
    ctx["current_user"] = username


@when(parsers.cfparse("the user logs in with username {username} and password {password}"))
def login_when(username, password, ctx, http_client):
    login_step(username, password, ctx, http_client)


@given(parsers.cfparse("the user has created a group channel named {name}"))
def create_group_channel(name: str, ctx: dict, http_client: httpx.Client):
    actor = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/channels/group",
        json={"name": name},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 201, resp.text
    ctx["channels"][name] = resp.json()["channel_id"]


@given(
    parsers.cfparse("the user has sent a message to {name} with body {body}")
)
def send_message_step(name: str, body: str, ctx: dict, http_client: httpx.Client):
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.post(
        f"/api/v1/channels/{channel_id}/messages",
        json={"body": body},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 201, resp.text


@when(parsers.cfparse("the user asks the copilot: {question}"))
def ask_copilot(question: str, ctx: dict, http_client: httpx.Client):
    actor = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/copilot/query",
        json={"question": question, "top_k": 5},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


@when("the user fetches their copilot usage")
def fetch_usage(ctx: dict, http_client: httpx.Client):
    actor = ctx["current_user"]
    resp = http_client.get(
        "/api/v1/copilot/usage",
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    ctx["last_usage"] = resp.json()


@then("the response status is 200")
def assert_status_200(ctx: dict):
    assert ctx["last_response"].status_code == 200, (
        f"expected 200, got {ctx['last_response'].status_code}; "
        f"body={ctx['last_response'].text}"
    )


@then("the total_calls is 0")
def assert_total_calls_zero(ctx: dict):
    assert ctx["last_usage"]["total_calls"] == 0, (
        f"Bob must NOT see Alice's copilot rows; total_calls="
        f"{ctx['last_usage']['total_calls']} (expected 0)"
    )


# ─── Refresh-token isolation ───────────────────────────────────────────


@when("the runtime role attempts to SELECT from rw_refresh_token directly")
def runtime_role_direct_select(actor_conn):
    """Open a cursor as `rw_app_login` and try a SELECT against
    `rw_refresh_token`. Migration 0140 REVOKEd table privileges from
    `rw_app`, so this statement should fail with
    `permission denied for table rw_refresh_token`.

    We stash the exception (if any) on the connection so the next
    step can assert the specific PG error code.
    """
    try:
        with actor_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM rw_refresh_token")
        actor_conn._last_rls_error = None
    except Exception as e:
        actor_conn._last_rls_error = e
    actor_conn.rollback()


@then("the direct SELECT is rejected with permission denied")
def assert_permission_denied(actor_conn):
    err = getattr(actor_conn, "_last_rls_error", None)
    assert err is not None, (
        "expected a permission-denied error from a direct SELECT on "
        "rw_refresh_token as rw_app_login; the query unexpectedly "
        "succeeded (table privileges were not revoked)"
    )
    msg = str(err).lower()
    assert "permission denied" in msg or "42501" in str(err), (
        f"expected 'permission denied' (SQLSTATE 42501), got: {err!r}"
    )


@when("the runtime role attempts to SELECT from rw_refresh_token directly")
def runtime_role_direct_select(actor_conn):
    """Open a cursor as `rw_app_login` and try a SELECT against
    `rw_refresh_token`. Migration 0140 REVOKEd table privileges from
    `rw_app`, so this statement should fail with
    `permission denied for table rw_refresh_token`.

    We stash the exception (if any) on the connection so the next
    step can assert the specific PG error code.
    """
    try:
        with actor_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM rw_refresh_token")
        actor_conn._last_rls_error = None
    except Exception as e:
        actor_conn._last_rls_error = e
    actor_conn.rollback()


@then("the direct SELECT is rejected with permission denied")
def assert_permission_denied(actor_conn):
    err = getattr(actor_conn, "_last_rls_error", None)
    assert err is not None, (
        "expected a permission-denied error from a direct SELECT on "
        "rw_refresh_token as rw_app_login; the query unexpectedly "
        "succeeded (table privileges were not revoked)"
    )
    msg = str(err).lower()
    assert "permission denied" in msg or "42501" in str(err), (
        f"expected 'permission denied' (SQLSTATE 42501), got: {err!r}"
    )