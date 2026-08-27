"""Adapter smoke tests — gated by `RUN_AI_SMOKE=1`.

Per `ai-provider-integration` / Step 9: "Adapter smoke tests hit the
real endpoint with one short prompt; gated by env var
(`RUN_AI_SMOKE=1`). Skipped in CI by default; run manually before
merging changes to the adapters."

**The agent must NEVER run these tests automatically.** They require
real API keys in the environment. The user controls when keys are
added to `.env` (per the project workflow) — the agent stops and
asks for the keys before running `RUN_AI_SMOKE=1`.

Run manually:

```bash
export MISTRAL_API_KEY=...
export NVIDIA_API_KEY=...
export RUN_AI_SMOKE=1
backend/.venv/bin/python -m pytest backend/tests/infrastructure/ai/ -v
```

Never record real API keys in tests or fixtures.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_AI_SMOKE"),
    reason="RUN_AI_SMOKE not set; live API smoke test",
)


@pytest.mark.skipif(
    not os.getenv("MISTRAL_API_KEY"),
    reason="MISTRAL_API_KEY not set",
)
def test_mistral_embed_smoke() -> None:
    """One-shot embed against `mistral-embed`. Verifies the SDK import
    path + the 1024-dim output. No retry assertions — just that the
    adapter works end-to-end against the real endpoint."""
    from app.infrastructure import MistralAdapter

    adapter = MistralAdapter(api_key=os.environ["MISTRAL_API_KEY"])
    vecs = adapter.embed(["hola", "hello"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    assert vecs[0] != vecs[1]  # not all-zeros


@pytest.mark.skipif(
    not os.getenv("NVIDIA_API_KEY"),
    reason="NVIDIA_API_KEY not set",
)
def test_nvidia_chat_smoke() -> None:
    """One-shot chat against `mistralai/mistral-nemotron` via NVIDIA NIM.
    Verifies the OpenAI-compatible wire shape + token usage parsing."""
    from app.infrastructure import NvidiaAdapter

    adapter = NvidiaAdapter(
        api_key=os.environ["NVIDIA_API_KEY"],
        default_model="mistralai/mistral-nemotron",
    )
    text, usage = adapter.chat(
        system="You are a test assistant. Reply with 'pong'.",
        user="ping",
        temperature=0.0,
    )
    assert isinstance(text, str)
    assert text.strip() != ""
    assert usage.model == "mistralai/mistral-nemotron"
    assert usage.prompt_tokens >= 0
    assert usage.completion_tokens >= 0