"""Module-level fake chat provider for the copilot BDD scenarios.

The pushback step (`tests/step_defs/test_copilot.py::push_back`)
mutates `_next_response` so the same provider returns a different
canned answer on the second call — modelling the safe-comply path.

Module-level state is gross but it's the simplest way to share
mutable test fixtures across pytest-bdd step files (per-module
scoping rules). The fixture is reset to the default "answer"
response before each scenario via the `_seed_fake` fixture below.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

# Mutable state — `_next_response` is what the fake chat provider
# returns on its next `chat(...)` call. Default is the canned
# "normal answer" used by the non-pushback scenarios.
_next_response: str | None = None


def _reset_default() -> None:
    global _next_response
    # Default: a "rich answer" with citations, so the dev demo (and
    # the BDD unit tests for the happy path) land on a non-denial
    # response. The BDD scenarios that need a denial explicitly set
    # the response via `set_response(...)` (or the test_copilot.py
    # pushback step mutates `_next_response` directly).
    _next_response = (
        "According to the visible context, the team has been "
        "discussing the project. Here is what was said "
        "[aaaaaaaa-1111-1111-1111-111111111111] and "
        "[bbbbbbbb-1111-1111-1111-111111111111]."
    )


_reset_default()


def set_response(text: str) -> None:
    """Set the fake's next canned answer (step defs use this to model
    the model's safe-comply response, the deny:no-permission reply,
    etc.). The next `chat(...)` call returns this exact text."""
    global _next_response
    _next_response = text


# ─── Fixture — resets the fake chat provider's state before each scenario
# (moved to conftest.py so it always runs, even for tests that don't
# import from this module).


@dataclass
class FakeEmbeddingProvider:
    """Deterministic 1024-dim embedding — `[1.0, 0.0, …]` for every
    text. Real dimensions (1024) match the schema's `vector(1024)`
    so the SQL cast succeeds.

    The BDD step defs do NOT verify the similarity math (the model
    + the pgvector distance are out of scope for the unit / BDD
    layer); they only verify RLS gating + the use-case orchestration.
    """

    dim: int = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


@dataclass
class FakeChatProvider:
    """Returns whatever `_next_response` is currently set to. The
    BDD step `push_back` mutates the module-level state."""

    model: str = "fake-copilot-model"
    prompt_tokens: int = 50
    completion_tokens: int = 25
    calls: list[dict] = field(default_factory=list)

    def chat(
        self, *, system: str, user: str, temperature: float = 0.2,
        model: str | None = None,
    ) -> tuple[str, "ChatUsage"]:
        from app.domain import ChatUsage

        self.calls.append({"system": system, "user": user, "model": model})
        return (
            _next_response or "I don't know.",
            ChatUsage(
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                model=self.model,
            ),
        )


# ─── Fixture — resets the fake chat provider's state before each scenario


import pytest


@pytest.fixture(autouse=True)
def _reset_fake_chat_state():
    """Reset the fake chat provider's next-response to the default
    'normal answer' before every copilot BDD scenario so pushback
    scenarios don't leak state into the next test."""
    _reset_default()
    yield
    _reset_default()