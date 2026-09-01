"""Step definitions for the messages BDD feature.

End-to-end against the pgvector testcontainer via FastAPI TestClient.
Every step runs as `rw_app_login` (no BYPASSRLS); RLS is the only
visibility filter — exactly mirroring production.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

_FEATURE_FILE = Path(__file__).resolve().parents[1] / "features" / "messages.feature"
scenarios(str(_FEATURE_FILE))


# ─── Fixtures & helpers ────────────────────────────────────────────────


@pytest.fixture
def msg_ctx() -> dict:
    """Per-scenario state: channel ids, message ids, edit counts."""
    return {
        "channels": {},      # name → channel_id
        "messages": {},      # client_ref → message_id
        "first_message_id": None,
        "second_message_id": None,
        "edit_message_id": None,
        "delete_message_id": None,
        "history_message_id": None,
        "read_message_id": None,
        "non_member_message_id": None,
        "edit_count": 0,
        "history_page1": None,
    }


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


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
    assert resp.status_code == 201, resp.text
    ctx["users"][username] = password
    ctx.setdefault("messages", {})


@given(parsers.cfparse("a user with username {username} and password {password} exists"))
def user_exists(username, password, ctx, http_client):
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


@given(
    parsers.cfparse(
        "the user has sent a message to {name} with client_ref {client_ref} and body {body}"
    )
)
def send_message_step(
    name: str, client_ref: str, body: str,
    ctx: dict, http_client: httpx.Client, msg_ctx: dict,
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
    msg_id = resp.json()["rw_id"]
    ctx["messages"][client_ref] = msg_id
    msg_ctx["edit_message_id"] = msg_id
    msg_ctx["delete_message_id"] = msg_id
    msg_ctx["history_message_id"] = msg_id
    msg_ctx["read_message_id"] = msg_id
    msg_ctx["non_member_message_id"] = msg_id


@given(parsers.cfparse("the user has sent {count:d} messages to {name}"))
def send_n_messages(
    count: int, name: str,
    ctx: dict, http_client: httpx.Client, msg_ctx: dict,
):
    """Send `count` messages with auto-generated bodies m0, m1, m2, …"""
    name = _unquote(name)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    for i in range(count):
        body = f"m{i}"
        resp = http_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            json={"body": body, "client_ref": f"seed-{name}-{i}"},
            headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
        )
        assert resp.status_code == 201, resp.text


# ─── When steps ─────────────────────────────────────────────────────────


@when(
    parsers.cfparse(
        "the user sends a message to {name} with client_ref {client_ref} and body {body}"
    )
)
def send_when(
    name: str, client_ref: str, body: str,
    ctx: dict, http_client: httpx.Client, msg_ctx: dict,
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
    ctx["last_response"] = resp
    if resp.status_code in (200, 201):
        msg_id = resp.json()["rw_id"]
        if msg_ctx["first_message_id"] is None:
            msg_ctx["first_message_id"] = msg_id
        else:
            msg_ctx["second_message_id"] = msg_id


@when(parsers.cfparse("the user edits that message with body {body}"))
def edit_step(body: str, ctx: dict, http_client: httpx.Client, msg_ctx: dict):
    body = _unquote(body)
    actor = ctx["current_user"]
    resp = http_client.patch(
        f"/api/v1/messages/{msg_ctx['edit_message_id']}",
        json={"body": body},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user logically deletes that message with reason {reason}"))
def delete_step(reason: str, ctx: dict, http_client: httpx.Client, msg_ctx: dict):
    reason = _unquote(reason)
    actor = ctx["current_user"]
    resp = http_client.post(
        f"/api/v1/messages/{msg_ctx['delete_message_id']}/delete",
        json={"reason": reason},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user requests the first page of {name} with limit {limit:d}"))
def history_first_page(
    name: str, limit: int,
    ctx: dict, http_client: httpx.Client, msg_ctx: dict,
):
    name = _unquote(name)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.get(
        f"/api/v1/channels/{channel_id}/messages",
        params={"limit": limit},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    msg_ctx["history_page1"] = resp.json()


@when(parsers.cfparse("the user requests the history of {name}"))
def history_step(name: str, ctx: dict, http_client: httpx.Client):
    name = _unquote(name)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.get(
        f"/api/v1/channels/{channel_id}/messages",
        params={"limit": 100},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


@when("the user marks that message as read")
def mark_read_step(ctx: dict, http_client: httpx.Client, msg_ctx: dict):
    actor = ctx["current_user"]
    resp = http_client.post(
        f"/api/v1/messages/{msg_ctx['read_message_id']}/read",
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp


@when(parsers.cfparse("the user logs in with username {username} and password {password}"))
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


# ─── Then steps ─────────────────────────────────────────────────────────


@then(parsers.cfparse("the response status is {code:d}"))
def assert_status(code: int, ctx: dict):
    actual = ctx["last_response"].status_code
    assert actual == code, (
        f"expected {code}, got {actual}; body={ctx['last_response'].text}"
    )


@then("the response contains a message id")
def assert_message_id(ctx: dict):
    body = ctx["last_response"].json()
    assert body.get("rw_id")
    assert uuid.UUID(body["rw_id"])  # parses as UUID


@then("the first and second message ids are the same")
def assert_same_ids(msg_ctx: dict):
    assert msg_ctx["first_message_id"] is not None
    assert msg_ctx["second_message_id"] is not None
    assert msg_ctx["first_message_id"] == msg_ctx["second_message_id"]


@then(parsers.cfparse("the response body is {body}"))
def assert_body(body: str, ctx: dict):
    body = _unquote(body)
    assert ctx["last_response"].json()["rw_body"] == body


@then("the response is_edited is true")
def assert_edited(ctx: dict):
    assert ctx["last_response"].json()["rw_is_edited"] is True


@then("the original message body is unchanged")
def assert_body_unchanged(msg_ctx: dict, super_conn):
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_body FROM rw_message WHERE rw_id = %s",
            (msg_ctx["edit_message_id"],),
        )
        actual = cur.fetchone()[0]
    assert actual == "alice wrote this", (
        f"expected the original body 'alice wrote this', got {actual!r}"
    )


@then("the original message is not marked deleted")
def assert_not_marked_deleted(msg_ctx: dict, super_conn):
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_deleted_at FROM rw_message WHERE rw_id = %s",
            (msg_ctx["delete_message_id"],),
        )
        deleted_at = cur.fetchone()[0]
    assert deleted_at is None, (
        f"non-author DELETE must NOT touch the row; rw_deleted_at={deleted_at!r}"
    )


@then(
    parsers.cfparse(
        "the database has exactly one rw_message_edit row for that message"
    )
)
def assert_edit_row(msg_ctx: dict, super_conn):
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM rw_message_edit WHERE rw_message_id = %s",
            (msg_ctx["edit_message_id"],),
        )
        assert cur.fetchone()[0] == 1


@then(
    parsers.cfparse(
        "the database has the message with rw_deleted_at set and reason {reason}"
    )
)
def assert_deleted(reason: str, msg_ctx: dict, super_conn):
    reason = _unquote(reason)
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_deleted_at, rw_deleted_reason FROM rw_message "
            "WHERE rw_id = %s",
            (msg_ctx["delete_message_id"],),
        )
        deleted_at, actual_reason = cur.fetchone()
        assert deleted_at is not None
        assert actual_reason == reason


@then(parsers.cfparse("the message {body} does not appear in the history"))
def assert_not_in_history(body: str, ctx: dict):
    items = ctx["last_response"].json()["items"]
    bodies = [it["rw_body"] for it in items]
    assert body not in bodies, f"{body!r} found in {bodies!r}"


@then(
    parsers.cfparse(
        "the page contains messages {a} and {b} only"
    )
)
def assert_page_two_messages(a: str, b: str, msg_ctx: dict):
    items = msg_ctx["history_page1"]["items"]
    bodies = [it["rw_body"] for it in items]
    assert set(bodies) == {a, b}, f"expected {{{a}, {b}}}, got {bodies!r}"


@then(parsers.cfparse("the next_cursor points at {body}"))
def assert_cursor(body: str, msg_ctx: dict, ctx: dict):
    items = msg_ctx["history_page1"]["items"]
    target = next((it for it in items if it["rw_body"] == body), None)
    assert target is not None, f"{body!r} not in page; items={items}"
    cur = msg_ctx["history_page1"]
    assert cur["next_cursor_id"] == target["rw_id"], (
        f"next_cursor_id={cur['next_cursor_id']}, expected {target['rw_id']}"
    )


@then(
    parsers.cfparse(
        "the database has exactly one rw_message_read row for that user"
    )
)
def assert_read_count(msg_ctx: dict, ctx: dict, super_conn):
    actor_id = _actor_id_from_username(
        ctx["current_user"], super_conn
    )
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM rw_message_read "
            "WHERE rw_message_id = %s AND rw_user_id = %s",
            (msg_ctx["read_message_id"], actor_id),
        )
        assert cur.fetchone()[0] == 1


@then("the history contains 0 items")
def assert_empty_history(ctx: dict):
    assert ctx["last_response"].json()["items"] == []


def _actor_id_from_username(username: str, super_conn) -> UUID:
    with super_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_id FROM rw_user WHERE rw_username = %s", (username,)
        )
        row = cur.fetchone()
        assert row is not None, f"user {username!r} not in DB"
        return row[0]
