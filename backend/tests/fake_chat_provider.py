"""Fake providers for the AI copilot — used by BDD tests and dev mode.

Two modes of operation:
1. **BDD tests** — use module-level `set_response()` + `push_back`
   mutating `_SHARED_RESPONSE` to model the safe-comply path.
2. **Dev mode / unit tests** — instantiate `FakeChatProvider()`
   which has its own `self._response` (default: rich answer with
   citations). No module state shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Shared mutable state — ONLY used by the BDD pushback step and
# `set_response()` helper. Dev mode creates its own instance with
# its own `_response` attribute.
_SHARED_RESPONSE: str | None = None


def _reset_default() -> None:
    global _SHARED_RESPONSE
    # Default for BDD: `deny:insufficient-context` so Scenario C
    # (safe-comply) works without an explicit Given step.
    _SHARED_RESPONSE = (
        "The visible history does not contain that information."
    )


def set_response(text: str) -> None:
    """Set the BDD shared next canned answer. Used by
    `Given the copilot will respond with ...` steps."""
    global _SHARED_RESPONSE
    _SHARED_RESPONSE = text


_reset_default()


@dataclass
class FakeEmbeddingProvider:
    """Deterministic 1024-dim embedding — `[1.0, 0.0, …]` for every
    text. Real dimensions (1024) match the schema's `vector(1024)`."""

    dim: int = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


@dataclass
class FakeChatProvider:
    """Fake chat provider with its own response state.
    
    - BDD tests: use the module-level `_SHARED_RESPONSE` via
      `FakeChatProvider(use_shared=True)` so `set_response()` /
      `push_back` work across step files.
    - Dev mode / unit tests: default instance has its own rich
      default response (citations, confidence=high).
    """

    model: str = "fake-copilot-model"
    prompt_tokens: int = 50
    completion_tokens: int = 25
    use_shared: bool = False
    _response: str | None = None
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.use_shared and self._response is None:
            # Dev mode default: rich answer with citations
            self._response = (
                "According to the visible context, the team has been "
                "discussing the project. Here is what was said "
                "[aaaaaaaa-1111-1111-1111-111111111111] and "
                "[bbbbbbbb-1111-1111-1111-111111111111]."
            )

    def chat(
        self, *, system: str, user: str, temperature: float = 0.2,
        model: str | None = None,
    ) -> tuple[str, "ChatUsage"]:
        from app.domain import ChatUsage

        self.calls.append({"system": system, "user": user, "model": model})
        if self.use_shared:
            text = _SHARED_RESPONSE or "I don't know."
        else:
            text = self._response or "I don't know."
        return (
            text,
            ChatUsage(
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                model=self.model,
            ),
        )


# ─── Fixture — resets the BDD shared state before each scenario


import pytest


@pytest.fixture(autouse=True)
def _reset_fake_chat_state():
    """Reset the BDD shared response to the default
    `deny:insufficient-context` before every copilot BDD scenario
    so pushback scenarios don't leak state into the next test."""
    _reset_default()
    yield
    _reset_default()