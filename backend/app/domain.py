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


class SessionFactory(Protocol):
    """Callable that yields a new psycopg connection.

    The session takes care of opening + committing + rolling back.
    Implementations can pull from a pool or open a fresh connection
    (the testcontainer fixture does the latter).
    """

    def __call__(self) -> "PsycopgConnection": ...


# Imported lazily to avoid pulling psycopg into the domain layer.
from psycopg import Connection as PsycopgConnection  # noqa: E402  (type-only import)
