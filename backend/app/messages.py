"""Messages use cases (Send / Edit / Delete / ChannelHistory / MarkRead).

Per ARCHITECTURE.md §5.1, use cases are thin: validate input, dispatch
to a port (or DB function), and map results.

The three human-review checks from issue #4:

1. **`rw_send_message` idempotency** — verify `ON CONFLICT DO NOTHING` +
   `RETURNING` pattern. Asserted by the BDD scenario `Send is idempotent
   on rw_client_ref` — same client_ref returns the original row + 200
   (not 201 + duplicate).
2. **Pagination SQL** — keyset `(rw_created_at, rw_id) <` + composite
   index `(rw_channel_id, rw_created_at DESC, rw_id DESC)`. Asserted
   by the BDD scenario `Keyset pagination is stable under concurrent
   insert` — insert at boundary, scroll up, no duplicates/skips.
3. **Edit append pattern** — `rw_message_edit` rows are INSERT-only.
   Asserted by the BDD scenario `Edit appends a rw_message_edit row`.

The full write path goes through DB functions/procedures defined in
Phase 1 (`0040_functions_procedures.sql`). The application layer never
INSERTs into `rw_message` directly; that would skip the membership
check + the embedding trigger + the idempotency contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from .domain import (
    Message,
    MessageRepository,
    SearchHit,
    SearchRepository,
    SessionFactory,
)
from .infrastructure import RwSession


# ─── Errors ─────────────────────────────────────────────────────────────


class MessageError(Exception):
    """Any message-flow failure. The `code` field drives the HTTP
    status in `app.delivery_messages._status_for`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MessageSummary:
    """Returned by the API. Field names match the JSON wire shape so
    the frontend can render them without translation."""

    rw_id: UUID
    rw_channel_id: UUID
    rw_author_id: UUID
    rw_body: str
    rw_is_edited: bool
    rw_created_at: datetime
    rw_edited_at: datetime | None
    # The actor's own state — useful for "is this mine?" badges in the UI.
    is_mine: bool


@dataclass(frozen=True)
class HistoryPage:
    items: list[MessageSummary]
    # Newest-first keyset cursor (None = first page, no further history).
    next_cursor: tuple[datetime, UUID] | None


# ─── SendMessage ────────────────────────────────────────────────────────


class SendMessage:
    """Send a message to a channel. Idempotent on `client_ref`.

    The DB function `rw_send_message(...)` (Phase 1, 0040) implements
    the idempotency contract via `ON CONFLICT DO NOTHING + RETURNING`:
    if a row with `(rw_author_id, rw_client_ref) WHERE client_ref IS
    NOT NULL` already exists, the original row is returned and the
    use case surfaces `idempotent_replay = True` so the API can
    return 200 (not 201) for the replay.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory

    def __call__(
        self,
        *,
        actor_id: UUID,
        channel_id: UUID,
        body: str,
        client_ref: str | None = None,
    ) -> tuple[MessageSummary, bool]:
        # ── Input validation ─────────────────────────────────────────
        if not (1 <= len(body) <= 8000):
            raise MessageError(
                "invalid-body", "body length must be 1..8000"
            )
        if client_ref is not None and not (1 <= len(client_ref) <= 64):
            raise MessageError(
                "invalid-client-ref", "client_ref length must be 1..64"
            )

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            msg, replay = repo.send_idempotent(
                channel_id=channel_id,
                author_id=actor_id,
                body=body,
                client_ref=client_ref,
            )
        return (
            MessageSummary(
                rw_id=msg.rw_id,
                rw_channel_id=msg.rw_channel_id,
                rw_author_id=msg.rw_author_id,
                rw_body=msg.rw_body,
                rw_is_edited=msg.rw_is_edited,
                rw_created_at=msg.rw_created_at,
                rw_edited_at=msg.rw_edited_at,
                is_mine=msg.rw_author_id == actor_id,
            ),
            replay,
        )


# ─── EditMessage ────────────────────────────────────────────────────────


class EditMessage:
    """Replace the body of an existing message; append a `rw_message_edit`
    row inside the same DB procedure (Phase 1, 0040). The original
    message body is preserved in the edit history; the row is never
    physically deleted (AGENTS.md / Prohibited Actions).
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory

    def __call__(
        self,
        *,
        actor_id: UUID,
        message_id: UUID,
        new_body: str,
    ) -> bool:
        if not (1 <= len(new_body) <= 8000):
            raise MessageError(
                "invalid-body", "body length must be 1..8000"
            )

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            return repo.edit(
                message_id=message_id,
                editor_id=actor_id,
                new_body=new_body,
            )


# ─── DeleteMessage ─────────────────────────────────────────────────────


class DeleteMessage:
    """Logical delete: sets `rw_deleted_at` + `rw_deleted_reason`. The row
    stays in `rw_message` (audit trail) and is invisible to
    `rw_visible_message` (history queries).
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory

    def __call__(
        self,
        *,
        actor_id: UUID,
        message_id: UUID,
        reason: str = "user-deleted",
    ) -> bool:
        if not (1 <= len(reason) <= 500):
            raise MessageError(
                "invalid-reason", "reason length must be 1..500"
            )

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            return repo.logical_delete(
                message_id=message_id,
                actor_id=actor_id,
                reason=reason,
            )


# ─── ChannelHistory ────────────────────────────────────────────────────


class ChannelHistory:
    """Keyset pagination over `rw_message`, newest first.

    Per ARCHITECTURE.md §6 + the issue review checklist, OFFSET is
    forbidden — keyset is the only allowed pagination form. The
    composite index `(rw_channel_id, rw_created_at DESC, rw_id DESC)`
    (Phase 1, 0030) supports the `(rw_created_at, rw_id) <` predicate
    without a sort.

    `before` is the cursor tuple `(rw_created_at, rw_id)` of the
    OLDEST message currently displayed; the next page returns messages
    strictly older. RLS does the visibility filter; the application
    layer only adds the logical-delete predicate (`rw_deleted_at IS
    NULL`) so a stale cursor that points at a now-deleted row still
    terminates the page rather than looping.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
        max_limit: int = 100,
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory
        self._max_limit = max_limit

    def __call__(
        self,
        *,
        actor_id: UUID,
        channel_id: UUID,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> HistoryPage:
        # Clamp the limit; reject zero / negative explicitly so the
        # caller doesn't get an empty page silently.
        if limit < 1 or limit > self._max_limit:
            raise MessageError(
                "invalid-limit",
                f"limit must be 1..{self._max_limit}",
            )

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            messages = repo.history_keyset(
                channel_id=channel_id,
                before=before,
                limit=limit,
            )

        # Build summaries and the next cursor (None when this page is
        # not full — i.e. no more history to load).
        summaries = [
            MessageSummary(
                rw_id=m.rw_id,
                rw_channel_id=m.rw_channel_id,
                rw_author_id=m.rw_author_id,
                rw_body=m.rw_body,
                rw_is_edited=m.rw_is_edited,
                rw_created_at=m.rw_created_at,
                rw_edited_at=m.rw_edited_at,
                is_mine=m.rw_author_id == actor_id,
            )
            for m in messages
        ]
        next_cursor = None
        if len(messages) == limit and messages:
            oldest = messages[-1]
            next_cursor = (oldest.rw_created_at, oldest.rw_id)

        return HistoryPage(items=summaries, next_cursor=next_cursor)


# ─── MarkRead ──────────────────────────────────────────────────────────


class MarkRead:
    """Insert a `(message_id, user_id)` row into `rw_message_read` if
    absent. Idempotent — the UNIQUE constraint on
    `(rw_message_id, rw_user_id)` makes it safe to retry.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory

    def __call__(self, *, actor_id: UUID, message_id: UUID) -> bool:
        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            return repo.mark_read(message_id=message_id, user_id=actor_id)


# ─── Phase 5: search + bulk mark-read + per-channel unread ──────────────


@dataclass(frozen=True)
class SearchHitSummary:
    """Wire shape for one search result. The `rw_highlight` field is
    HTML — the frontend renders it as `dangerouslySetInnerHTML` after
    a sanitization pass on `<mark>` only (Phase 5 leaves the full body
    un-escaped for the regex-free path; the React render uses the
    `rw_body` field for the fallback)."""

    rw_id: UUID
    rw_channel_id: UUID
    rw_author_id: UUID
    rw_body: str
    rw_created_at: datetime
    rw_highlight: str
    is_mine: bool


class SearchMessages:
    """Phase 5: lexical search in one channel with `ts_headline`.

    The DB function `rw_search_messages(...)` does the heavy lifting:
    locale is pulled from `rw_user.rw_locale`, the highlight uses
    `<mark>` tags, and a non-member gets an empty result set (the
    function checks channel membership as defense in depth because
    SECURITY DEFINER bypasses RLS).

    The use case validates input + projects the entity. It does NOT
    sort, filter, or rewrite the highlight — that's the DB's job.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        search_repo_factory: Callable[..., SearchRepository],
        max_limit: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._search_repo_factory = search_repo_factory
        self._max_limit = max_limit

    def __call__(
        self,
        *,
        actor_id: UUID,
        channel_id: UUID,
        query: str,
        limit: int = 20,
    ) -> list[SearchHitSummary]:
        if not (1 <= len(query) <= 200):
            raise MessageError(
                "invalid-query", "query length must be 1..200"
            )
        if limit < 1 or limit > self._max_limit:
            raise MessageError(
                "invalid-limit",
                f"limit must be 1..{self._max_limit}",
            )

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._search_repo_factory(conn)
            hits = repo.search_in_channel(
                channel_id=channel_id, query=query, limit=limit
            )

        return [
            SearchHitSummary(
                rw_id=h.rw_id,
                rw_channel_id=h.rw_channel_id,
                rw_author_id=h.rw_author_id,
                rw_body=h.rw_body,
                rw_created_at=h.rw_created_at,
                rw_highlight=h.rw_highlight,
                is_mine=h.rw_author_id == actor_id,
            )
            for h in hits
        ]


class MarkChannelRead:
    """Phase 5: bulk mark all visible messages in a channel as read.

    Used when the user opens the conversation view; the channel's
    unread badge clears on the next `list_visible_with_unread()` call.
    Idempotent (the DB UNIQUE constraint swallows the no-op inserts).
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory

    def __call__(self, *, actor_id: UUID, channel_id: UUID) -> int:
        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            return repo.mark_channel_read(
                channel_id=channel_id, user_id=actor_id
            )


class UnreadCountForChannel:
    """Phase 5: get the actor's unread count for one channel.

    Kept as a dedicated use case (not just an inline repo call from
    the router) so the cache / pre-compute paths Phase 7 introduces
    don't reach across layers.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        message_repo_factory: Callable[..., MessageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._message_repo_factory = message_repo_factory

    def __call__(self, *, actor_id: UUID, channel_id: UUID) -> int:
        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._message_repo_factory(conn)
            return repo.unread_count_for_channel(
                channel_id=channel_id, user_id=actor_id
            )
