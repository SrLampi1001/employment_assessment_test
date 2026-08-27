"""AI Copilot use case — Phase 6 (issue #7, ARCHITECTURE.md §4).

The copilot is **permission-filtered by RLS, not by the LLM**. Per
ARCH §4, the context window is filled from `rw_visible_message`
after the same `app.current_user_id` GUC has filtered the rows —
the same posture as `GET /messages/`. The LLM never sees a row the
actor couldn't see via the direct API.

The audit row in `rw_copilot_usage` is inserted **unconditionally**
— success or failure, tokens or zero tokens. The §11.4 report
groups by `rw_user_id` and a missing row would be a regression.

The four refusal / inference codes live in
`references/denial-taxonomy.md` and are taught to the model via the
versioned system prompt (`PROMPT_VERSION` in `app.copilot_prompt`).
The frontend renders each code with its own banner colour; the BDD
tests assert on the literal strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .copilot_prompt import (
    CITATION_FORMAT_HINT,
    PROMPT_VERSION,
    build_system_prompt,
    render_user_prompt,
)
from .domain import (
    ChatProvider,
    ChatUsage,
    CopilotUsageRepository,
    EmbeddingProvider,
    MessageRepository,
    ProviderError,
    RetrievedChunk,
    TransientProviderError,
)


# ─── Errors ─────────────────────────────────────────────────────────────


class CopilotError(Exception):
    """Any copilot-flow failure. The `code` field drives the HTTP
    status in `app.delivery_copilot._status_for`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ─── DTOs ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Citation:
    """One source message the LLM cited. The frontend renders each
    citation as a clickable chip that scrolls to the message in the
    conversation view."""

    rw_id: UUID
    rw_channel_id: UUID
    snippet: str  # the body, truncated to ~120 chars by the use case


@dataclass(frozen=True)
class CopilotAnswer:
    """Wire shape for `POST /api/v1/copilot/query`.

    `denial_code` is None for a normal answer, one of the four
    taxonomy codes (`deny:no-permission`, `deny:out-of-scope`,
    `deny:insufficient-context`, `infer:low-confidence`) when the
    model refused or flagged. `confidence` is `"low"` for the
    safe-comply path; normal answers carry `"high"`.

    Per `references/denial-taxonomy.md`, all four are HTTP 200 — the
    refusal IS the answer.
    """

    text: str
    citations: list[Citation]
    denial_code: str | None
    confidence: str  # "low" | "high"
    prompt_version: str


# ─── Settings contract ──────────────────────────────────────────────────


class _CopilotSettings(Protocol):
    """Subset of `Settings` the use case depends on. A Protocol so the
    unit tests can pass a tiny stub."""

    chat_model_primary: str
    chat_model_fallback: str
    chat_temperature: float


# ─── Use case ──────────────────────────────────────────────────────────


class AskCopilot:
    """The single Phase 6 use case. Orchestrates: embed question →
    RLS-filtered retrieval → chat (with fallback) → audit insert.

    The audit insert is **always** called, even on provider failure
    (with zero tokens) — that's the §11.4 audit-trail contract and
    the human-review check from issue #7.
    """

    # The literal marker the safe-comply response MUST begin with.
    # The BDD test (`infer:low-confidence` after pushback) asserts
    # on it verbatim. The model is taught this exact string in the
    # system prompt.
    LOW_CONFIDENCE_MARKER = "Inferred with incomplete context: Confidence LOW"

    # Denial / inference codes — kept in sync with the system prompt
    # + the denial-taxonomy reference.
    DENY_NO_PERMISSION = "deny:no-permission"
    DENY_OUT_OF_SCOPE = "deny:out-of-scope"
    DENY_INSUFFICIENT_CONTEXT = "deny:insufficient-context"
    INFER_LOW_CONFIDENCE = "infer:low-confidence"

    def __init__(
        self,
        session_factory,
        *,
        message_repo_factory,
        usage_repo_factory,
        embedder: EmbeddingProvider,
        chatter: ChatProvider,
        settings: _CopilotSettings,
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory
        self._usage_repo_factory = usage_repo_factory
        self._embed = embedder
        self._chat = chatter
        self._settings = settings

    def __call__(
        self,
        *,
        actor_id: UUID,
        question: str,
        top_k: int = 8,
    ) -> CopilotAnswer:
        if not (1 <= len(question) <= 1000):
            raise CopilotError(
                "invalid-question", "question length must be 1..1000"
            )
        if top_k < 1 or top_k > 50:
            raise CopilotError(
                "invalid-top-k", "top_k must be 1..50"
            )

        # 1. Embed the question. Failures from the embedder are
        #    non-recoverable for THIS call — surface them as 503.
        try:
            q_vec = self._embed.embed([question])[0]
        except TransientProviderError as e:
            self._audit_failure(actor_id, self._settings.chat_model_primary)
            raise CopilotError(
                "embedding-unavailable",
                "embedding provider is busy; please retry",
            ) from e
        except ProviderError as e:
            self._audit_failure(actor_id, self._settings.chat_model_primary)
            raise CopilotError(
                "embedding-unavailable",
                f"embedding provider error: {e}",
            ) from e

        # 2. Retrieve under RLS. Empty list is the **signal** for
        #    `deny:no-permission` (non-member or membership-lost).
        with _sync_session(self._session_factory, actor_id) as conn:
            message_repo = self._message_repo_factory(conn)
            chunks = message_repo.search_similar(
                actor_id=actor_id, embedding=q_vec, limit=top_k
            )

        # 3. Render the prompt + call chat (with primary → fallback).
        system = build_system_prompt(citation_format=CITATION_FORMAT_HINT)
        user = render_user_prompt(question=question, context=chunks)

        answer_text, usage, used_fallback = self._chat_with_fallback(
            system=system, user=user
        )

        # 4. Classify the response (denial taxonomy).
        denial_code, confidence = self._classify(
            answer_text=answer_text, context_size=len(chunks)
        )

        # 5. Build citations. Truncate the body to 120 chars for the
        #    snippet so the frontend chip stays compact.
        citations = [
            Citation(
                rw_id=chunk.rw_id,
                rw_channel_id=chunk.rw_channel_id,
                snippet=(
                    (chunk.rw_body[:119] + "…")
                    if len(chunk.rw_body) > 120
                    else chunk.rw_body
                ),
            )
            for chunk in chunks
        ]

        # 6. **Unconditional** audit insert — even on denial /
        #    inference, the row lands so the §11.4 report knows
        #    the actor asked and the provider answered.
        self._audit(
            actor_id=actor_id,
            model=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

        return CopilotAnswer(
            text=answer_text,
            citations=citations,
            denial_code=denial_code,
            confidence=confidence,
            prompt_version=PROMPT_VERSION,
        )

    # ── chat fallback ───────────────────────────────────────────────

    def _chat_with_fallback(
        self, *, system: str, user: str
    ) -> tuple[str, ChatUsage, bool]:
        """Primary → fallback chain. Both calls share the same system
        prompt. Returns `(text, usage, used_fallback)`."""
        try:
            text, usage = self._chat.chat(
                system=system,
                user=user,
                temperature=self._settings.chat_temperature,
                model=self._settings.chat_model_primary,
            )
            return text, usage, False
        except (TransientProviderError, ProviderError) as primary_exc:
            try:
                text, usage = self._chat.chat(
                    system=system,
                    user=user,
                    temperature=self._settings.chat_temperature,
                    model=self._settings.chat_model_fallback,
                )
                return text, usage, True
            except (TransientProviderError, ProviderError) as fallback_exc:
                # Both providers failed — surface the fallback error
                # (it's the most recent one the caller would see).
                raise CopilotError(
                    "provider-unavailable",
                    f"primary ({primary_exc}) and fallback "
                    f"({fallback_exc}) both failed",
                ) from fallback_exc

    # ── classification ──────────────────────────────────────────────

    def _classify(
        self, *, answer_text: str, context_size: int
    ) -> tuple[str | None, str]:
        """Map the model's response to a `(denial_code, confidence)` pair.

        Rules (in priority order):
        1. Empty context → deny:no-permission regardless of the answer.
        2. Answer starts with the literal `Inferred with incomplete
           context: Confidence LOW` marker → infer:low-confidence +
           confidence="low".
        3. Answer contains "do not have access" / "outside the scope"
           heuristics → deny:* family (the model is taught these
           strings in the system prompt).
        4. Otherwise → no denial, confidence="high".
        """
        if context_size == 0:
            return self.DENY_NO_PERMISSION, "low"

        stripped = answer_text.strip()
        if stripped.startswith(self.LOW_CONFIDENCE_MARKER):
            return self.INFER_LOW_CONFIDENCE, "low"

        lowered = stripped.lower()
        if "do not have access" in lowered:
            return self.DENY_NO_PERMISSION, "low"
        if (
            "outside the scope" in lowered
            or "unrelated to internal messaging" in lowered
        ):
            return self.DENY_OUT_OF_SCOPE, "low"
        if (
            "visible history does not contain" in lowered
            or "does not contain that information" in lowered
        ):
            return self.DENY_INSUFFICIENT_CONTEXT, "low"

        return None, "high"

    # ── audit helpers ───────────────────────────────────────────────

    def _audit(
        self,
        *,
        actor_id: UUID,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        with _sync_session(self._session_factory, actor_id) as conn:
            repo = self._usage_repo_factory(conn)
            repo.record(
                actor_id=actor_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

    def _audit_failure(self, actor_id: UUID, model: str) -> None:
        """Audit hook for embed failures (zero tokens, model=name)."""
        self._audit(
            actor_id=actor_id,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
        )


# ─── sync session helper (consistent with RwSession elsewhere) ──────


from contextlib import contextmanager

from .infrastructure import RwSession


@contextmanager
def _sync_session(session_factory, actor_id: UUID):
    """Re-export of `RwSession` as a context manager — keeps the
    copilot use case from importing infrastructure types directly
    while still using the same transaction-local GUC contract as the
    rest of the app."""
    with RwSession(session_factory, actor_id=actor_id) as conn:
        yield conn