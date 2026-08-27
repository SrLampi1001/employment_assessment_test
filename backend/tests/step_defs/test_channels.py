"""Step definitions for the channels BDD feature.

End-to-end against the pgvector testcontainer via FastAPI TestClient.
Every step runs as `rw_app_login` (no BYPASSRLS) so RLS is in force;
this is the same posture as the Phase 1 + Phase 2 BDD suites.

The `ctx` fixture is a mutable dict shared across the scenario's
steps; it carries the user-credentials dict, the per-user JWT pair,
and the most recent HTTP response so `Then` steps can inspect it
without re-fetching.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from pytest_bdd import given, parsers, scenarios, then, when

_FEATURE_FILE = Path(__file__).resolve().parents[1] / "features" / "channels.feature"
scenarios(str(_FEATURE_FILE))


def _unquote(s: str) -> str:
    """cfparse captures Cucumber-style quoted strings; for steps that
    use `{name}` (untyped) the captured value keeps the surrounding
    quotes. Strip them so channel keys match the JSON payload's
    `name` field exactly."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _user_id_lookup(
    http_client: httpx.Client,
    ctx: dict,
    username: str,
    *,
    as_user: str | None = None,
) -> str:
    """Look up a user_id by username via /users/search.

    `/users/search` requires an authenticated actor. We pick the
    currently-logged-in user (or the explicit `as_user` override) so
    the search hits the same DB session the use case would.
    """
    actor = as_user or next(iter(ctx["tokens"]))
    resp = http_client.get(
        "/api/v1/users/search",
        params={"q": username, "limit": 1},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 200, resp.text
    for u in resp.json():
        if u["rw_username"] == username:
            return u["rw_id"]
    raise AssertionError(f"user {username!r} not found via /users/search")


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
    ctx["users"][username] = password


@given(parsers.cfparse("a user with username {username} and password {password} exists"))
def user_exists(
    username: str,
    password: str,
    ctx: dict,
    http_client: httpx.Client,
):
    register_via_http(username, password, ctx, http_client)


@given(parsers.cfparse("the user has logged in with username {username} and password {password}"))
def login_and_capture(
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
    # Set the current actor so subsequent steps know which user's
    # JWT to use without depending on dict-insertion order.
    ctx["current_user"] = username


# ─── When steps ─────────────────────────────────────────────────────────


@when(parsers.cfparse("the user creates a group channel named {name}"))
def create_group_channel(
    name: str,
    ctx: dict,
    http_client: httpx.Client,
):
    name = _unquote(name)
    username = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/channels/group",
        json={"name": name},
        headers={"Authorization": f"Bearer {ctx['tokens'][username]['access']}"},
    )
    ctx["last_response"] = resp
    if resp.status_code == 201:
        body = resp.json()
        ctx["channels"][name] = body["channel_id"]


@when(parsers.cfparse("the user adds bob to the channel {name}"))
def add_bob_to_channel(
    name: str,
    ctx: dict,
    http_client: httpx.Client,
):
    name = _unquote(name)
    inviter = ctx["current_user"]
    bob_id = _user_id_lookup(http_client, ctx, "bob")
    channel_id = ctx["channels"][name]
    resp = http_client.post(
        f"/api/v1/channels/{channel_id}/members",
        json={"new_member_id": bob_id, "role": 1},
        headers={"Authorization": f"Bearer {ctx['tokens'][inviter]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user logs in with username {username} and password {password}"))
def login_step(
    username: str,
    password: str,
    ctx: dict,
    http_client: httpx.Client,
):
    login_and_capture(username, password, ctx, http_client)
    ctx["current_user"] = username


@when("the user lists the visible channels")
@then("the user lists the visible channels")
def list_channels(ctx: dict, http_client: httpx.Client):
    username = ctx["current_user"]
    resp = http_client.get(
        "/api/v1/channels",
        headers={"Authorization": f"Bearer {ctx['tokens'][username]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user tries to read the channel named {name}"))
@then(parsers.cfparse("the user tries to read the channel named {name}"))
def try_read_channel(
    name: str,
    ctx: dict,
    http_client: httpx.Client,
):
    # The actor attempts a channel-scoped endpoint. Phase 3 doesn't
    # yet expose GET /channels/{id}; we use the DELETE endpoint as a
    # probe — a non-member of an invisible channel gets 404 from the
    # leave use case (RLS hides the row, find returns None, use case
    # raises channel-not-found).
    name = _unquote(name)
    channel_id = ctx["channels"][name]
    username = ctx["current_user"]
    resp = http_client.delete(
        f"/api/v1/channels/{channel_id}",
        headers={"Authorization": f"Bearer {ctx['tokens'][username]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user leaves the channel named {name}"))
def leave_channel(name: str, ctx: dict, http_client: httpx.Client):
    name = _unquote(name)
    channel_id = ctx["channels"][name]
    username = ctx["current_user"]
    resp = http_client.delete(
        f"/api/v1/channels/{channel_id}",
        headers={"Authorization": f"Bearer {ctx['tokens'][username]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user creates a direct channel with username {username}"))
def create_direct_channel(
    username: str,
    ctx: dict,
    http_client: httpx.Client,
):
    actor = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/channels/direct",
        json={"other_username": username},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


# ─── Then steps ─────────────────────────────────────────────────────────


@then(parsers.cfparse("the response status is {code:d}"))
def assert_status(code: int, ctx: dict):
    actual = ctx["last_response"].status_code
    assert actual == code, (
        f"expected {code}, got {actual}; body={ctx['last_response'].text}"
    )


@then("the response contains the channel id")
def assert_channel_id(ctx: dict):
    body = ctx["last_response"].json()
    assert "channel_id" in body
    assert body["channel_id"]


@then(parsers.cfparse("the response kind is {kind:d}"))
def assert_kind(kind: int, ctx: dict):
    body = ctx["last_response"].json()
    assert body["kind"] == kind


@then(parsers.cfparse("the response includes a channel named {name}"))
def assert_includes_channel(name: str, ctx: dict):
    name = _unquote(name)
    body = ctx["last_response"].json()
    items = body.get("items", body)  # GET /channels returns {items: ...}
    names = [it["name"] for it in items]
    assert name in names, (
        f"expected channel {name!r} in visible list, got {names!r}"
    )


@then(parsers.cfparse("the response does not include a channel named {name}"))
def assert_excludes_channel(name: str, ctx: dict):
    name = _unquote(name)
    body = ctx["last_response"].json()
    items = body.get("items", body)
    names = [it["name"] for it in items]
    assert name not in names, (
        f"expected channel {name!r} to be gone, got {names!r}"
    )


@then(parsers.cfparse("that channel's my_role is {role:d}"))
def assert_my_role(role: int, ctx: dict):
    body = ctx["last_response"].json()
    items = body.get("items", body)
    # The most recent assertion (assumes the previous step left the
    # channel list in ctx["last_response"]). For robustness, search all
    # channels and check at least one matches the role; the test is
    # written against a freshly-created channel list so this is unique.
    matched = [it for it in items if it["my_role"] == role]
    assert matched, (
        f"expected a channel with my_role={role}, got roles: "
        f"{[it['my_role'] for it in items]}"
    )
