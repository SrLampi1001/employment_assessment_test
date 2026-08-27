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


class SessionFactory(Protocol):
    """Callable that yields a new psycopg connection.

    The session takes care of opening + committing + rolling back.
    Implementations can pull from a pool or open a fresh connection
    (the testcontainer fixture does the latter).
    """

    def __call__(self) -> "PsycopgConnection": ...


# Imported lazily to avoid pulling psycopg into the domain layer.
from psycopg import Connection as PsycopgConnection  # noqa: E402  (type-only import)
