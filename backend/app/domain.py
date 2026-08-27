"""Domain entities + ports (Protocols).

Per ARCHITECTURE.md §5.2, the domain is **pure Python** — no FastAPI,
no psycopg, no pydantic-settings. Ports are `typing.Protocol` so any
adapter can satisfy them without inheritance gymnastics.

The three ports the auth flow needs:

- `PasswordHasher` — argon2id for human-chosen passwords (low-entropy).
- `JwtService` — short-lived access tokens. **Carries `sub` only**;
  membership / role are resolved per request from the database.
- `RefreshTokenStore` — Postgres-backed store; family-reuse revocation
  happens in a single SQL UPDATE (the test asserts the WHERE covers
  the family, not just the row).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


# ─── Entities ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class User:
    rw_id: UUID
    rw_username: str
    rw_display_name: str
    rw_locale: str
    rw_created_at: datetime


@dataclass(frozen=True)
class Channel:
    rw_id: UUID
    rw_name: str
    rw_kind: int  # 1 = direct, 2 = group (see ChannelKind)
    rw_created_by: UUID
    rw_created_at: datetime


@dataclass(frozen=True)
class ChannelMember:
    rw_id: UUID
    rw_channel_id: UUID
    rw_user_id: UUID
    rw_role: int  # 1 = member, 2 = owner (see ChannelRole)
    rw_joined_at: datetime
    rw_left_at: datetime | None


# Phase 3 — channel kind + role enums (smallint values match the DB).
DIRECT = 1
GROUP = 2
MEMBER = 1
OWNER = 2


@dataclass(frozen=True)
class RefreshTokenRecord:
    rw_id: UUID
    rw_user_id: UUID
    rw_token_hash: str
    rw_family_id: UUID
    rw_expires_at: datetime
    rw_revoked_at: datetime | None


@dataclass(frozen=True)
class Message:
    rw_id: UUID
    rw_channel_id: UUID
    rw_author_id: UUID
    rw_client_ref: str | None
    rw_body: str
    rw_is_edited: bool
    rw_created_at: datetime
    rw_edited_at: datetime | None
    rw_deleted_at: datetime | None
    rw_deleted_reason: str | None


@dataclass(frozen=True)
class MessageEdit:
    rw_id: UUID
    rw_message_id: UUID
    rw_body: str
    rw_edited_at: datetime
    rw_editor_id: UUID


# ─── Phase 5: search entity ─────────────────────────────────────────────


@dataclass(frozen=True)
class SearchHit:
    """One row from `rw_search_messages(...)`. The `rw_highlight`
    field is the original `rw_body` with `<mark>…</mark>` around the
    matching tokens (the locale is the actor's `rw_locale` from the DB).

    The frontend renders this verbatim — escaping happens in the
    client because we control both sides of the wire and React
    already escapes text by default. `<mark>` is a harmless HTML tag.
    """

    rw_id: UUID
    rw_channel_id: UUID
    rw_author_id: UUID
    rw_body: str
    rw_created_at: datetime
    rw_highlight: str


# ─── Phase 5: per-channel read-state view ────────────────────────────────


@dataclass(frozen=True)
class ChannelReadState:
    """Snapshot of a channel from the perspective of the actor."""

    channel: Channel
    membership: ChannelMember
    unread_count: int


# ─── Ports ───────────────────────────────────────────────────────────────


class PasswordHasher(Protocol):
    """Argon2id for human passwords (slow on purpose)."""

    def hash(self, plaintext: str) -> str: ...
    def verify(self, stored_hash: str, plaintext: str) -> bool: ...


class JwtService(Protocol):
    """Short-lived access token. `sub` = user_id, nothing else."""

    def issue_access(self, user_id: UUID) -> str: ...
    def decode_access(self, token: str) -> UUID: ...


class RefreshTokenStore(Protocol):
    """Persistence boundary for refresh tokens.

    `revoke_family` is the security-critical call — it MUST be one SQL
    statement with `WHERE rw_family_id = %s`, not a Python loop. A test
    asserts the contract.
    """

    def insert(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        family_id: UUID,
        expires_at: datetime,
    ) -> None: ...

    def find_by_hash(self, token_hash: str) -> RefreshTokenRecord | None: ...

    def revoke(self, token_id: UUID) -> None: ...

    def revoke_family(self, family_id: UUID) -> None: ...


class UserRepository(Protocol):
    def find_by_username(self, username: str) -> tuple[User, str] | None:
        """Returns `(user, password_hash)`. None if the username is unknown."""
        ...

    def search_by_username_prefix(self, prefix: str, limit: int) -> list[User]:
        """Find users whose username starts with `prefix`. Used by the
        channel-member invite UI to suggest users to add. RLS on
        `rw_user` is not enabled (it carries no private data), so this
        is a straight SELECT. Capped at `limit`."""
        ...


class ChannelRepository(Protocol):
    def list_visible(self) -> list[tuple[Channel, ChannelMember]]:
        """Returns `(channel, actor_membership)` pairs for every channel
        the actor is a current member of. RLS gates the read; the join
        to `rw_channel_member` is also RLS-filtered to the actor's own
        membership rows (the policy is `rw_user_id = GUC`)."""
        ...

    def find(self, channel_id: UUID) -> Channel | None:
        """Returns the channel if the actor can see it; `None` otherwise
        (RLS returns no rows when the actor is not a member)."""
        ...

    def create(self, *, name: str, kind: int, creator_id: UUID) -> UUID:
        """Thin wrapper around `rw_create_channel(...)`. Returns the new
        channel id. The DB function inserts the channel + the creator's
        owner membership in one statement (Phase 1, 0040)."""
        ...

    def list_visible_with_unread(self) -> list[tuple[Channel, ChannelMember, int]]:
        """Phase 5: same as `list_visible` but each row carries the
        actor's unread count for that channel. Backs the channel list
        in the UI so the unread badge can render without an extra
        round-trip per channel."""
        ...


class ChannelMemberRepository(Protocol):
    def add(self, *, channel_id: UUID, inviter_id: UUID,
            new_member_id: UUID, role: int = MEMBER) -> ChannelMember:
        """Calls `rw_add_channel_member(...)`. Raises the function's
        EXCEPTIONs as `ChannelError` subclasses in the use case."""
        ...

    def leave(self, *, channel_id: UUID, user_id: UUID) -> bool:
        """Sets `rw_left_at = now()` on the actor's current membership.
        Returns True if a row was updated, False if the actor was not
        a current member. Idempotent — leaving twice is a no-op."""
        ...


class MessageRepository(Protocol):
    """Persistence boundary for messages + keyset history.

    `send_idempotent` returns either the freshly-inserted row OR the
    existing row (when `(rw_author_id, rw_client_ref) WHERE
    rw_client_ref IS NOT NULL` collides). The `idempotent_replay` flag
    tells the use case which path was taken — a `True` return is a
    "no-op", not a duplicate insert.
    """

    def send_idempotent(
        self,
        *,
        channel_id: UUID,
        author_id: UUID,
        body: str,
        client_ref: str | None,
    ) -> tuple[Message, bool]:
        """Returns `(message, was_replay)`. Uses `rw_send_message(...)`
        (Phase 1, 0040) which has `ON CONFLICT DO NOTHING + RETURNING`
        semantics; on replay, the existing row is selected."""
        ...

    def find_visible(self, message_id: UUID, viewer_id: UUID) -> Message | None:
        """Read a single message. RLS-gated: returns `None` when the
        viewer is not a current member of the channel."""
        ...

    def history_keyset(
        self,
        *,
        channel_id: UUID,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Message]:
        """Keyset page of visible messages, newest first.

        Cursor tuple `(created_at, id)` is the strict-less-than bound.
        `None` cursor means "start from the latest". RLS does the
        visibility filter; we add `WHERE rw_deleted_at IS NULL` so the
        view stays consistent with `rw_visible_message`.
        """
        ...

    def edit(self, *, message_id: UUID, editor_id: UUID, new_body: str) -> bool:
        """Calls `rw_edit_message(...)`. Returns True if the message
        was updated, False if it didn't exist / was deleted / the
        editor isn't the author."""
        ...

    def logical_delete(
        self,
        *,
        message_id: UUID,
        actor_id: UUID,
        reason: str,
    ) -> bool:
        """Calls `rw_delete_message(...)`. Returns True if the row was
        marked deleted, False if the actor wasn't the author / the row
        was already deleted / it didn't exist."""
        ...

    def mark_read(self, *, message_id: UUID, user_id: UUID) -> bool:
        """Inserts a `(message_id, user_id)` row into `rw_message_read`
        if absent (the table has a UNIQUE constraint that makes this
        safe to retry). Returns True on insert."""
        ...

    def unread_count_for_channel(
        self, *, channel_id: UUID, user_id: UUID
    ) -> int:
        """Phase 5: count visible (non-logically-deleted) messages in
        this channel that the user has NOT marked read. Returns 0
        for non-members."""
        ...

    def mark_channel_read(self, *, channel_id: UUID, user_id: UUID) -> int:
        """Phase 5: bulk insert `rw_message_read` for every visible
        message that isn't already marked. Idempotent (UNIQUE on
        `(rw_message_id, rw_user_id)`). Returns the number of rows
        actually inserted (0 on a no-op call)."""
        ...


class SearchRepository(Protocol):
    """Phase 5: lexical search across `rw_message` with highlight.

    Locale comes from the actor's `rw_user.rw_locale` inside the DB
    function — the application does not pass it. RLS is bypassed
    inside the SECURITY DEFINER function but membership is re-checked
    explicitly, so a non-member gets zero rows by construction.
    """

    def search_in_channel(
        self, *, channel_id: UUID, query: str, limit: int
    ) -> list[SearchHit]:
        """`ts_headline(rw_locale, rw_body, plainto_tsquery(rw_locale, query))`
        with `<mark>` tags around matches. Returns newest-first."""
        ...


class SessionFactory(Protocol):
    """Callable that yields a new psycopg connection.

    The session takes care of opening + committing + rolling back.
    Implementations can pull from a pool or open a fresh connection
    (the testcontainer fixture does the latter).
    """

    def __call__(self) -> "PsycopgConnection": ...


# Imported lazily to avoid pulling psycopg into the domain layer.
from psycopg import Connection as PsycopgConnection  # noqa: E402  (type-only import)
