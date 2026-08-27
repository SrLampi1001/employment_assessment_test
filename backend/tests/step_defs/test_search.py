"""Step definitions for the Phase 5 search + read-receipts BDD feature.

End-to-end against the pgvector testcontainer via FastAPI TestClient.
Every step runs as `rw_app_login` (no BYPASSRLS); RLS is the only
visibility filter — exactly mirroring production.

Mirrors the patterns established in `test_messages.py` (Phase 4):
- cfparse with `{name}` placeholders, `_unquote(s)` helper.
- `ctx` / `msg_ctx` are pytest fixtures defined in `conftest.py`.
- Step defs are scoped to this module (pytest-bdd per-module rule).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

_FEATURE_FILE = (
    Path(__file__).resolve().parents[1] / "features" / "search.feature"
)
scenarios(str(_FEATURE_FILE))


# ─── Fixtures & helpers ────────────────────────────────────────────────


@pytest.fixture
def search_ctx() -> dict:
    """Per-scenario state for Phase 5."""
    return {
        "highlight": None,
        "items": None,
        "inserted": None,
        "channel_summary": None,
    }


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


# ─── Given steps (mostly re-used from test_messages.py) ────────────────


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
    assert resp.status_code == 201, resp.text
    ctx["users"][username] = password


@given(parsers.cfparse("a user with username {username} and password {password} exists"))
def user_exists(username, password, ctx, http_client):
    """Variant of `register_via_http` for scenarios that end in `... password secret exists`.
    The literal `exists` suffix is required by cfparse so the `{password}`
    capture isn't greedy and consume `secret exists` as one token."""
    register_via_http(username, password, ctx, http_client)


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


@given(parsers.cfparse("the user has created a group channel named {name}"))
def create_group_channel(name: str, ctx: dict, http_client: httpx.Client):
    name = _unquote(name)
    actor = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/channels/group",
        json={"name": name},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 201, resp.text
    ctx["channels"][name] = resp.json()["channel_id"]


@given(parsers.cfparse(
    "the user has sent a message to {name} with client_ref {client_ref} and body {body}"
))
def send_message_step(
    name: str, client_ref: str, body: str,
    ctx: dict, http_client: httpx.Client,
):
    name = _unquote(name)
    body = _unquote(body)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.post(
        f"/api/v1/channels/{channel_id}/messages",
        json={"body": body, "client_ref": client_ref},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 201, resp.text
    ctx.setdefault("messages", {})[client_ref] = resp.json()["rw_id"]


@given(parsers.cfparse("the user has sent {count:d} messages to {name}"))
def send_n_messages(
    count: int, name: str,
    ctx: dict, http_client: httpx.Client,
):
    name = _unquote(name)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    for i in range(count):
        resp = http_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            json={"body": f"m{i}", "client_ref": f"seed-{name}-{i}"},
            headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
        )
        assert resp.status_code == 201, resp.text


@given(parsers.cfparse(
    "the user has marked the message with client_ref {client_ref} as read"
))
def mark_one_read(
    client_ref: str,
    ctx: dict, http_client: httpx.Client,
):
    actor = ctx["current_user"]
    mid = ctx["messages"][client_ref]
    resp = http_client.post(
        f"/api/v1/messages/{mid}/read",
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 204, resp.text


@given(parsers.cfparse("the user logs in with username {username} and password {password}"))
def login_when(username, password, ctx, http_client):
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


# ─── When steps ─────────────────────────────────────────────────────────


@when(parsers.cfparse("the user searches for q={q} in {name}"))
def search_step(
    q: str, name: str,
    ctx: dict, http_client: httpx.Client, search_ctx: dict,
):
    q = _unquote(q)
    name = _unquote(name)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.get(
        f"/api/v1/channels/{channel_id}/search",
        params={"q": q, "limit": 20},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    body = resp.json()
    search_ctx["items"] = body["items"]


@when("the user lists channels")
def list_channels_step(
    ctx: dict, http_client: httpx.Client,
):
    actor = ctx["current_user"]
    resp = http_client.get(
        "/api/v1/channels",
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user marks the channel {name} as read"))
def mark_channel_read_step(
    name: str,
    ctx: dict, http_client: httpx.Client, search_ctx: dict,
):
    name = _unquote(name)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.post(
        f"/api/v1/channels/{channel_id}/read",
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    if resp.status_code == 200:
        search_ctx["inserted"] = resp.json()["inserted"]


# ─── Then steps ────────────────────────────────────────────────────────


@then(parsers.cfparse("the response status is {code:d}"))
def assert_status(code: int, ctx: dict):
    actual = ctx["last_response"].status_code
    assert actual == code, (
        f"expected {code}, got {actual}; body={ctx['last_response'].text}"
    )


@then(parsers.cfparse("the search response has exactly {count:d} item"))
def assert_search_count_single(count: int, search_ctx: dict):
    assert len(search_ctx["items"]) == count, (
        f"expected {count}, got {len(search_ctx['items'])}; "
        f"items={search_ctx['items']}"
    )


@then(parsers.cfparse("the search response has exactly {count:d} items"))
def assert_search_count_plural(count: int, search_ctx: dict):
    assert len(search_ctx["items"]) == count, (
        f"expected {count}, got {len(search_ctx['items'])}; "
        f"items={search_ctx['items']}"
    )


@then(parsers.cfparse(
    "the highlight of the first item contains {needle}"
))
def assert_highlight(needle: str, search_ctx: dict):
    needle = _unquote(needle)
    items = search_ctx["items"]
    assert items, "search returned no items but a highlight was expected"
    highlight = items[0]["rw_highlight"]
    assert needle in highlight, (
        f"expected {needle!r} in highlight {highlight!r}"
    )
    search_ctx["highlight"] = highlight


@then(parsers.cfparse("the inserted count is at least {n:d}"))
def assert_inserted_at_least(n: int, search_ctx: dict):
    assert search_ctx["inserted"] is not None, (
        "no /channels/{id}/read response captured"
    )
    assert search_ctx["inserted"] >= n, (
        f"expected >= {n}, got {search_ctx['inserted']}"
    )


@then(parsers.cfparse("the channel {name} has unread_count {value:d}"))
def assert_channel_unread(
    name: str, value: int,
    ctx: dict, search_ctx: dict,
):
    name = _unquote(name)
    body = ctx["last_response"].json()
    items = body.get("items") or []
    match = next((i for i in items if i["name"] == name), None)
    assert match is not None, (
        f"channel {name!r} not in list: {[i['name'] for i in items]}"
    )
    assert match["unread_count"] == value, (
        f"expected unread_count {value} for {name!r}, got "
        f"{match['unread_count']}"
    )
    search_ctx["channel_summary"] = match


@then("the response has 0 channels")
def assert_no_channels(ctx: dict):
    body = ctx["last_response"].json()
    items = body.get("items") or []
    assert items == [], f"expected empty list, got {items!r}"