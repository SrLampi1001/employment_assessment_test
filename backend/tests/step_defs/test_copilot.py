"""Step definitions for tests/features/copilot.feature.

End-to-end against the pgvector testcontainer via FastAPI TestClient
+ a `FakeEmbeddingProvider` + `FakeChatProvider` (no live Mistral
or NVIDIA calls — per ai-provider-integration / Step 9: "Adapter
smoke tests are gated by env var ... skipped in CI by default").

The two mandatory ARCHITECTURE.md §10 scenarios live in
test_membership.py (the SQL-level RLS gating for the copilot
vector path). This module covers:

- Scenario A (HTTP) — non-member gets `deny:no-permission` + zero
  citations via POST /api/v1/copilot/query.
- Scenario B (HTTP) — owner / member sees their own messages +
  citations via POST /api/v1/copilot/query.
- Scenario C (safe-comply) — insufficient-context denial →
  pushback → `infer:low-confidence` response carries the literal
  "Inferred with incomplete context: Confidence LOW" marker.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

_FEATURE_FILE = (
    Path(__file__).resolve().parents[1] / "features" / "copilot.feature"
)
scenarios(str(_FEATURE_FILE))


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def copilot_ctx() -> dict:
    """Per-scenario state — answers from previous steps, model
    overrides, etc."""
    return {
        "answer": None,
        "usage": None,
    }


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


# ─── Given steps (mirroring test_messages / test_search patterns) ─────


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
def user_exists_via_http(username, password, ctx, http_client):
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
    "the user has sent a message to {name} with body {body}"
))
def send_message_step(
    name: str, body: str,
    ctx: dict, http_client: httpx.Client,
):
    name = _unquote(name)
    body = _unquote(body)
    actor = ctx["current_user"]
    channel_id = ctx["channels"][name]
    resp = http_client.post(
        f"/api/v1/channels/{channel_id}/messages",
        json={"body": body},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    assert resp.status_code == 201, resp.text


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


@when(parsers.cfparse("the user asks the copilot: {question}"))
def ask_copilot_step(
    question: str,
    ctx: dict, http_client: httpx.Client, copilot_ctx: dict,
):
    question = _unquote(question)
    # Default to the canned "normal answer" (uses citations, classifies
    # as confidence="high"). The safe-comply scenario's first call
    # keeps the autouse fixture's default (insufficient-context denial).
    # Use `Given the copilot will respond with X` to override per
    # scenario.
    from tests.fake_chat_provider import set_response

    actor = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/copilot/query",
        json={"question": question, "top_k": 5},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    if resp.status_code == 200:
        copilot_ctx["answer"] = resp.json()


@given(parsers.cfparse("the copilot will respond with {text}"))
def set_fake_response(text: str):
    """Set the fake chat provider's next response. Useful for
    scenarios that need a canned denial / inference marker before
    the next `When the user asks the copilot: ...` step."""
    from tests.fake_chat_provider import set_response
    set_response(_unquote(text))


@when("the user pushes back: answer anyway")
def push_back(ctx: dict, http_client: httpx.Client, copilot_ctx: dict):
    """The user insists on a low-confidence answer after the initial
    insufficient-context denial. The same fake chat provider now
    returns the literal "Inferred with incomplete context: Confidence
    LOW" marker, modelling the model's safe-comply behaviour.

    The `http_client` fixture is wired with `FakeChatProvider(use_shared=True)`
    (see conftest.py) so this `set_response()` call mutates the BDD
    shared state that the live `chatter` reads on the next call.
    """
    from tests.fake_chat_provider import set_response

    set_response(
        "Inferred with incomplete context: Confidence LOW. "
        "Best guess based on no visible context."
    )
    actor = ctx["current_user"]
    resp = http_client.post(
        "/api/v1/copilot/query",
        json={"question": "please answer anyway", "top_k": 5},
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    if resp.status_code == 200:
        copilot_ctx["answer"] = resp.json()


@when("the user fetches their copilot usage")
def fetch_usage(
    ctx: dict, http_client: httpx.Client, copilot_ctx: dict,
):
    actor = ctx["current_user"]
    resp = http_client.get(
        "/api/v1/copilot/usage",
        headers={"Authorization": f"Bearer {ctx['tokens'][actor]['access']}"},
    )
    ctx["last_response"] = resp
    if resp.status_code == 200:
        copilot_ctx["usage"] = resp.json()


# ─── Then steps ─────────────────────────────────────────────────────────


@then(parsers.cfparse("the response status is {code:d}"))
def assert_status(code: int, ctx: dict):
    actual = ctx["last_response"].status_code
    assert actual == code, (
        f"expected {code}, got {actual}; body={ctx['last_response'].text}"
    )


@then(parsers.cfparse("the denial_code is {code}"))
def assert_denial_code(code: str, copilot_ctx: dict):
    code = _unquote(code)
    assert copilot_ctx["answer"]["denial_code"] == code, (
        f"expected denial_code={code!r}, got "
        f"{copilot_ctx['answer']['denial_code']!r}"
    )


@then("the citations list is empty")
def assert_no_citations(copilot_ctx: dict):
    assert copilot_ctx["answer"]["citations"] == []


@then(parsers.cfparse("the citations list has {n:d} items"))
def assert_citation_count(n: int, copilot_ctx: dict):
    assert len(copilot_ctx["answer"]["citations"]) == n, (
        f"expected {n} citations, got {len(copilot_ctx['answer']['citations'])}"
    )


@then(parsers.cfparse("the answer text starts with {marker}"))
def assert_answer_starts_with(marker: str, copilot_ctx: dict):
    marker = _unquote(marker)
    assert copilot_ctx["answer"]["text"].startswith(marker), (
        f"expected answer to start with {marker!r}, got "
        f"{copilot_ctx['answer']['text'][:80]!r}"
    )


@then(parsers.cfparse("the confidence is {value}"))
def assert_confidence(value: str, copilot_ctx: dict):
    value = _unquote(value)
    assert copilot_ctx["answer"]["confidence"] == value, (
        f"expected confidence={value!r}, got "
        f"{copilot_ctx['answer']['confidence']!r}"
    )


@then("the response contains a prompt_version")
def assert_prompt_version(copilot_ctx: dict):
    v = copilot_ctx["answer"]["prompt_version"]
    assert v
    # The PROMPT_VERSION is "2026-08-27.6" — just check the shape.
    assert v.startswith("2026-")


@then(parsers.cfparse("the total_calls is at least {n:d}"))
def assert_usage_calls_at_least(n: int, copilot_ctx: dict):
    actual = copilot_ctx["usage"]["total_calls"]
    assert actual >= n, f"expected >= {n} calls, got {actual}"