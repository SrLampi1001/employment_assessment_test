"""Auth use cases (Register / Login / Refresh).

Per ARCHITECTURE.md §5.1, use cases are thin: they validate input,
dispatch to a port (or DB function), and map results. No business
rules here — those live in the database (transactional functions,
RLS policies).

The three human-review checks from `docs/DECISIONS.md` are enforced
as unit tests:

1. **JWT middleware** — security boundary; verify the `sub`-only rule.
2. **Refresh rotation + family reuse detection** — the SQL transaction
   revokes the entire family, not just the row.
3. **Password hashing** — argon2id (not bcrypt, not MD5).

Wiring note: the use cases that touch the database take **class
types** (callables) for the conn-bound adapters (`UserRepository`,
`RefreshTokenStore`). The use case constructs an instance per request
inside its `RwSession`, so the lifetime matches the transaction. This
is the same pattern as `RwSession` itself taking a `SessionFactory`.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from .domain import (
    JwtService,
    PasswordHasher,
    RefreshTokenStore,
    SessionFactory,
    UserRepository,
)
from .infrastructure import RwSession


# ─── Shared types ───────────────────────────────────────────────────────


class AuthError(Exception):
    """Any auth flow failure. The `code` field drives the HTTP status.

    Codes are intentionally coarse — leaking `user-not-found` vs
    `wrong-password` is a username enumeration vector. Both are mapped
    to the same `invalid-credentials` code.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


_NEW_REFRESH_BYTES = 48  # 384 bits of entropy


def _new_refresh_token_plaintext() -> str:
    return secrets.token_urlsafe(_NEW_REFRESH_BYTES)


def _refresh_hash(token: str) -> str:
    """Hash a refresh token for storage / lookup.

    SHA-256 because refresh tokens are server-generated high-entropy
    random strings — argon2id is for low-entropy human passwords.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ─── RegisterUser ───────────────────────────────────────────────────────


class RegisterUser:
    """Creates a user + credential atomically via `rw_register_user(...)`.

    Validation is at the use-case boundary (caller-supplied input); the
    DB function enforces the locale check + UNIQUE constraint as the
    second layer (ARCHITECTURE.md §3 + §7).
    """

    def __init__(self, session_factory: SessionFactory, hasher: PasswordHasher) -> None:
        self._session_factory = session_factory
        self._hasher = hasher

    def __call__(
        self,
        *,
        username: str,
        display_name: str,
        locale: str,
        password: str,
    ) -> UUID:
        # ── Input validation ─────────────────────────────────────────
        if not (1 <= len(username) <= 64):
            raise AuthError("invalid-username", "username length must be 1..64")
        if not (1 <= len(display_name) <= 120):
            raise AuthError(
                "invalid-display-name", "display_name length must be 1..120"
            )
        if locale not in ("es", "en"):
            raise AuthError("invalid-locale", "locale must be 'es' or 'en'")
        if not (1 <= len(password) <= 128):
            raise AuthError("invalid-password", "password length must be 1..128")

        pw_hash = self._hasher.hash(password)
        with RwSession(self._session_factory, actor_id=None) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rw_register_user(%s, %s, %s, %s)",
                    (username, display_name, locale, pw_hash),
                )
                user_id = cur.fetchone()[0]
        return user_id


# ─── Login ──────────────────────────────────────────────────────────────


# Class types work as factories: `PostgresUserRepository(conn)` etc.
UserRepoFactory = Callable[..., UserRepository]
RefreshStoreFactory = Callable[..., RefreshTokenStore]


class Login:
    """Verify password + issue a fresh token pair (new family)."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        user_repo_factory: UserRepoFactory,
        refresh_store_factory: RefreshStoreFactory,
        hasher: PasswordHasher,
        jwt_service: JwtService,
        refresh_ttl_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._user_repo_factory = user_repo_factory
        self._refresh_store_factory = refresh_store_factory
        self._hasher = hasher
        self._jwt = jwt_service
        self._refresh_ttl_seconds = refresh_ttl_seconds

    def __call__(self, *, username: str, password: str) -> TokenPair:
        with RwSession(self._session_factory, actor_id=None) as conn:
            user_repo = self._user_repo_factory(conn)
            refresh_store = self._refresh_store_factory(conn)

            found = user_repo.find_by_username(username)
            if found is None:
                # Constant-time dummy verify: refuse to leak via timing
                # whether the username exists.
                self._hasher.verify(_DUMMY_ARGON2_HASH, password)
                raise AuthError(
                    "invalid-credentials", "invalid username or password"
                )
            user, stored_hash = found
            if not self._hasher.verify(stored_hash, password):
                raise AuthError(
                    "invalid-credentials", "invalid username or password"
                )

            access = self._jwt.issue_access(user.rw_id)
            refresh_plain = _new_refresh_token_plaintext()
            refresh_hash = _refresh_hash(refresh_plain)
            family_id = uuid4()
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=self._refresh_ttl_seconds
            )
            refresh_store.insert(
                user_id=user.rw_id,
                token_hash=refresh_hash,
                family_id=family_id,
                expires_at=expires_at,
            )
        return TokenPair(
            access_token=access,
            refresh_token=refresh_plain,
            refresh_expires_at=expires_at,
        )


# A pre-computed valid argon2id hash for the dummy verify path. The
# plaintext is irrelevant — only the verify call's CPU cost matters
# (to defeat timing oracles). Generated once at import time.
import argon2

_DUMMY_ARGON2_HASH = argon2.PasswordHasher().hash("dummy")


# ─── Refresh ────────────────────────────────────────────────────────────


class Refresh:
    """Rotate refresh tokens; reuse detection revokes the whole family.

    Two branches:
    1. **Happy path** — token is valid + not revoked + not expired →
       mark old revoked, issue new pair under the same `rw_family_id`.
    2. **Reuse detection** — token is already revoked OR not found →
       revoke the whole family (if we know its id). Pattern from
       [Auth0 refresh token rotation](https://auth0.com/docs/secure/tokens/refresh-tokens/refresh-token-rotation).
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        refresh_store_factory: RefreshStoreFactory,
        jwt_service: JwtService,
        refresh_ttl_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._refresh_store_factory = refresh_store_factory
        self._jwt = jwt_service
        self._refresh_ttl_seconds = refresh_ttl_seconds

    def __call__(self, *, refresh_token: str) -> TokenPair:
        token_hash = _refresh_hash(refresh_token)
        with RwSession(self._session_factory, actor_id=None) as conn:
            refresh_store = self._refresh_store_factory(conn)
            record = refresh_store.find_by_hash(token_hash)

            if record is None:
                # Reuse of a token we never issued. We can't revoke a
                # family (we don't know which one) — refuse with the
                # same 401 shape so the client treats it identically
                # to any other invalid token.
                raise AuthError(
                    "invalid-refresh-token", "refresh token not recognized"
                )

            if record.rw_revoked_at is not None:
                # ── Reuse detection ────────────────────────────────────
                # Revoke every non-revoked token in this family. Done in
                # a single SQL UPDATE — see the contract test. The
                # commit MUST happen here, not on __exit__, because
                # raising AuthError would roll back the security write.
                refresh_store.revoke_family(record.rw_family_id)
                conn.commit()
                raise AuthError(
                    "refresh-token-revoked",
                    "refresh token was already used; family revoked",
                )

            if record.rw_expires_at <= datetime.now(timezone.utc):
                raise AuthError("refresh-token-expired", "refresh token expired")

            # ── Happy path ─────────────────────────────────────────────
            refresh_store.revoke(record.rw_id)
            access = self._jwt.issue_access(record.rw_user_id)
            new_plain = _new_refresh_token_plaintext()
            new_hash = _refresh_hash(new_plain)
            new_expires = datetime.now(timezone.utc) + timedelta(
                seconds=self._refresh_ttl_seconds
            )
            refresh_store.insert(
                user_id=record.rw_user_id,
                token_hash=new_hash,
                family_id=record.rw_family_id,
                expires_at=new_expires,
            )
            return TokenPair(
                access_token=access,
                refresh_token=new_plain,
                refresh_expires_at=new_expires,
            )
