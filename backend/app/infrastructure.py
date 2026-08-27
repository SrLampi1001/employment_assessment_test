"""Infrastructure adapters + RwSession.

Layered per ARCHITECTURE.md §5.2:
- Domain (`app.domain`) is pure Python + `typing.Protocol`.
- This module implements those protocols with concrete adapters
  (argon2-cffi, PyJWT, psycopg).
- Adapters take a `psycopg.Connection` in their constructors so use
  cases can compose them inside an RwSession transaction (the
  application role never sees a connection that wasn't GUC-tagged).

`RwSession` is the security boundary the middleware + dependency
combination sets up per request. The pattern matches `ARCHITECTURE.md §7`
(actor propagation via `SET LOCAL app.current_user_id`).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from uuid import UUID

import jwt
from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from psycopg import Connection

from .domain import (
    Channel,
    ChannelMember,
    ChannelMemberRepository,
    ChannelRepository,
    DIRECT,
    GROUP,
    JwtService,
    MEMBER,
    Message,
    MessageEdit,
    MessageRepository,
    PasswordHasher,
    RefreshTokenRecord,
    RefreshTokenStore,
    SearchHit,
    SearchRepository,
    SessionFactory,
    User,
    UserRepository,
)


# ─── PasswordHasher: argon2id ────────────────────────────────────────────


class Argon2idHasher:
    """argon2id with the library defaults (sensible time + memory cost).

    Per AGENTS.md / ARCHITECTURE §7, argon2id is mandatory; bcrypt / MD5
    are explicitly invalidating conditions.
    """

    def __init__(self) -> None:
        self._ph = _Argon2()

    def hash(self, plaintext: str) -> str:
        return self._ph.hash(plaintext)

    def verify(self, stored_hash: str, plaintext: str) -> bool:
        try:
            self._ph.verify(stored_hash, plaintext)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False


# ─── Refresh-token hashing (NOT a PasswordHasher port) ──────────────────


class RefreshTokenHasher:
    """SHA-256 for refresh tokens.

    Refresh tokens are server-generated high-entropy random strings,
    so the slow KDF that protects low-entropy human passwords is not
    appropriate here. SHA-256 is constant-time-friendly with
    `hmac.compare_digest`.
    """

    @staticmethod
    def hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def verify(token: str, expected_hash: str) -> bool:
        return secrets.compare_digest(
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expected_hash,
        )


# ─── JwtService: PyJWT ──────────────────────────────────────────────────


class PyJwtService:
    """HS256 access JWT. `sub` = user_id; nothing else is signed in.

    Per AGENTS.md (Prohibited Actions): no role/membership claims, ever.
    Membership is re-resolved from the DB per transaction so a token
    outliving a role change cannot escalate the actor.
    """

    def __init__(
        self, secret: str, access_ttl_seconds: int, *, algorithm: str = "HS256"
    ) -> None:
        self._secret = secret
        self._ttl = access_ttl_seconds
        self._alg = algorithm

    def issue_access(self, user_id: UUID) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl)).timestamp()),
            "type": "access",
        }
        return jwt.encode(payload, self._secret, algorithm=self._alg)

    def decode_access(self, token: str) -> UUID:
        """Raise `jwt.PyJWTError` on any decode failure (expired, malformed,
        wrong signature, wrong type). The caller is responsible for
        mapping the failure to a 401.
        """
        payload = jwt.decode(token, self._secret, algorithms=[self._alg])
        if payload.get("type") != "access":
            raise jwt.InvalidTokenError(
                f"unexpected token type: {payload.get('type')!r}"
            )
        # `sub` is the ONLY identity claim.
        return UUID(payload["sub"])


# ─── RwSession: one transaction, one actor ──────────────────────────────


class RwSession:
    """Context manager: open a connection, `SET LOCAL app.current_user_id`,
    yield the connection, commit (or rollback on exception).

    Per ARCHITECTURE.md §7 + .agents/skills/postgresql-rls-pgvector
    (Step 4.1): the actor must be visible to the RLS policies on every
    statement in the request, including vector search and aggregations.
    `SET LOCAL` is bound to the current transaction, so closing the
    transaction (commit/rollback) automatically clears the actor —
    no risk of leakage into the next request that reuses the connection.
    """

    def __init__(self, session_factory: SessionFactory, actor_id: UUID | None) -> None:
        self._factory = session_factory
        self._actor_id = actor_id
        self._conn: Connection | None = None

    def __enter__(self) -> Connection:
        self._conn = self._factory()
        with self._conn.cursor() as cur:
            if self._actor_id is not None:
                cur.execute(
                    "SELECT set_config('app.current_user_id', %s, true)",
                    (str(self._actor_id),),
                )
            else:
                cur.execute("SELECT set_config('app.current_user_id', NULL, true)")
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._conn is not None
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


# ─── Postgres adapters ──────────────────────────────────────────────────


class PostgresUserRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def find_by_username(self, username: str) -> tuple[User, str] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT u.rw_id, u.rw_username, u.rw_display_name, "
                "       u.rw_locale, u.rw_created_at, c.rw_password_hash "
                "FROM rw_user u "
                "JOIN rw_auth_credential c ON c.rw_user_id = u.rw_id "
                "WHERE u.rw_username = %s",
                (username,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            user = User(
                rw_id=row[0],
                rw_username=row[1],
                rw_display_name=row[2],
                rw_locale=row[3],
                rw_created_at=row[4],
            )
            return user, row[5]

    def search_by_username_prefix(self, prefix: str, limit: int) -> list[User]:
        """Prefix search for the invite-user UI.

        `rw_user` has no RLS (carries no private data — locale + name
        are visible to any logged-in actor). The query is parameterized;
        no string concatenation.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_id, rw_username, rw_display_name, rw_locale, rw_created_at "
                "FROM rw_user "
                "WHERE rw_username ILIKE %s "
                "ORDER BY rw_username "
                "LIMIT %s",
                (f"{prefix}%", limit),
            )
            rows = cur.fetchall()
            return [
                User(
                    rw_id=r[0],
                    rw_username=r[1],
                    rw_display_name=r[2],
                    rw_locale=r[3],
                    rw_created_at=r[4],
                )
                for r in rows
            ]


class PostgresChannelRepository:
    """Channel reads + the channel-create DB call.

    Reads use `rw_channel` directly — the RLS policy on that table lets
    the actor see only channels they're a current member of, so a
    `SELECT *` returns the right list without explicit filters.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def list_visible(self) -> list[tuple[Channel, ChannelMember]]:
        """Channel + the actor's own membership (for the actor's role).

        The join to `rw_channel_member` is RLS-filtered to the actor's
        own rows (`rw_user_id = GUC`), so each `ch` row gets at most one
        matching membership — the actor's own.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT ch.rw_id, ch.rw_name, ch.rw_kind, ch.rw_created_by, "
                "       ch.rw_created_at, "
                "       m.rw_id, m.rw_channel_id, m.rw_user_id, m.rw_role, "
                "       m.rw_joined_at, m.rw_left_at "
                "FROM rw_channel ch "
                "JOIN rw_channel_member m ON m.rw_channel_id = ch.rw_id "
                "WHERE m.rw_left_at IS NULL "
                "ORDER BY ch.rw_created_at DESC"
            )
            rows = cur.fetchall()
            return [
                (
                    Channel(
                        rw_id=r[0],
                        rw_name=r[1],
                        rw_kind=r[2],
                        rw_created_by=r[3],
                        rw_created_at=r[4],
                    ),
                    ChannelMember(
                        rw_id=r[5],
                        rw_channel_id=r[6],
                        rw_user_id=r[7],
                        rw_role=r[8],
                        rw_joined_at=r[9],
                        rw_left_at=r[10],
                    ),
                )
                for r in rows
            ]

    def find(self, channel_id: UUID) -> Channel | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_id, rw_name, rw_kind, rw_created_by, rw_created_at "
                "FROM rw_channel WHERE rw_id = %s AND rw_deleted_at IS NULL",
                (channel_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return Channel(*row)

    def create(self, *, name: str, kind: int, creator_id: UUID) -> UUID:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_create_channel(%s, %s, %s)",
                (name, kind, creator_id),
            )
            return cur.fetchone()[0]

    def list_visible_with_unread(
        self,
    ) -> list[tuple[Channel, ChannelMember, int]]:
        """Phase 5: list visible channels with the actor's per-channel
        unread count.

        The unread count is computed per channel via
        `rw_unread_count_for_channel(...)` (Phase 5, 0120). The
        function is SECURITY DEFINER with explicit membership +
        GUC-actor checks, so the count is correctly zero for
        non-members (defense in depth on top of RLS).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT ch.rw_id, ch.rw_name, ch.rw_kind, ch.rw_created_by, "
                "       ch.rw_created_at, "
                "       m.rw_id, m.rw_channel_id, m.rw_user_id, m.rw_role, "
                "       m.rw_joined_at, m.rw_left_at "
                "FROM rw_channel ch "
                "JOIN rw_channel_member m ON m.rw_channel_id = ch.rw_id "
                "WHERE m.rw_left_at IS NULL "
                "ORDER BY ch.rw_created_at DESC"
            )
            rows = cur.fetchall()
            out: list[tuple[Channel, ChannelMember, int]] = []
            for r in rows:
                channel = Channel(
                    rw_id=r[0], rw_name=r[1], rw_kind=r[2],
                    rw_created_by=r[3], rw_created_at=r[4],
                )
                membership = ChannelMember(
                    rw_id=r[5], rw_channel_id=r[6], rw_user_id=r[7],
                    rw_role=r[8], rw_joined_at=r[9], rw_left_at=r[10],
                )
                cur.execute(
                    "SELECT rw_unread_count_for_channel(%s, %s)",
                    (channel.rw_id, membership.rw_user_id),
                )
                unread = cur.fetchone()[0] or 0
                out.append((channel, membership, int(unread)))
            return out


class PostgresChannelMemberRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(
        self,
        *,
        channel_id: UUID,
        inviter_id: UUID,
        new_member_id: UUID,
        role: int = MEMBER,
    ) -> ChannelMember:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_id, rw_channel_id, rw_user_id, rw_role, "
                "       rw_joined_at, rw_left_at "
                "FROM rw_add_channel_member(%s, %s, %s, %s)",
                (channel_id, inviter_id, new_member_id, role),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(
                    "rw_add_channel_member returned no row (should not happen)"
                )
            return ChannelMember(*row)

    def leave(self, *, channel_id: UUID, user_id: UUID) -> bool:
        """Idempotent leave: returns True iff a row was just changed.

        The actor updates their own row (`rw_user_id = GUC`), which is
        allowed by the RLS policy (FOR ALL … USING rw_user_id = GUC).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE rw_channel_member "
                "SET rw_left_at = now() "
                "WHERE rw_channel_id = %s "
                "  AND rw_user_id    = %s "
                "  AND rw_left_at   IS NULL",
                (channel_id, user_id),
            )
            return cur.rowcount > 0


class PostgresMessageRepository:
    """Postgres-backed message persistence.

    All write paths go through `rw_send_message(...)`,
    `rw_edit_message(...)`, and `rw_delete_message(...)` — the
    Security-Definer functions defined in Phase 1 (0040). The actor
    GUC is set by `RwSession`; the functions re-check it as defense
    in depth.

    Reads (`history_keyset`, `find_visible`) use RLS — non-members
    see zero rows automatically. `rw_deleted_at IS NULL` keeps the
    history consistent with the `rw_visible_message` view (logical
    delete must NEVER be bypassed by a different code path).
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def send_idempotent(
        self,
        *,
        channel_id: UUID,
        author_id: UUID,
        body: str,
        client_ref: str | None,
    ) -> tuple[Message, bool]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM rw_send_message(%s, %s, %s, %s)",
                (channel_id, author_id, body, client_ref),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(
                    "rw_send_message returned no row (should not happen)"
                )
            # `out_was_replay` is the first OUT param of the function
            # (Phase 4 migration 0110). The remaining columns are the
            # rw_message row, in their schema order.
            was_replay = bool(row[0])
            msg = self._row_to_message(row[1:])
            return msg, was_replay

    def find_visible(
        self, message_id: UUID, viewer_id: UUID
    ) -> Message | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_id, rw_channel_id, rw_author_id, rw_client_ref, "
                "       rw_body, rw_is_edited, rw_created_at, rw_edited_at, "
                "       rw_deleted_at, rw_deleted_reason "
                "FROM rw_message "
                "WHERE rw_id = %s AND rw_deleted_at IS NULL",
                (message_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return self._row_to_message(row)

    def history_keyset(
        self,
        *,
        channel_id: UUID,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Message]:
        # Keyset predicate: (rw_created_at, rw_id) < (cursor_ts, cursor_id).
        # The composite index `(rw_channel_id, rw_created_at DESC, rw_id DESC)`
        # (Phase 1, 0030_indexes.sql) supports this without an OFFSET scan.
        if before is None:
            sql = (
                "SELECT rw_id, rw_channel_id, rw_author_id, rw_client_ref, "
                "       rw_body, rw_is_edited, rw_created_at, rw_edited_at, "
                "       rw_deleted_at, rw_deleted_reason "
                "FROM rw_message "
                "WHERE rw_channel_id = %s AND rw_deleted_at IS NULL "
                "ORDER BY rw_created_at DESC, rw_id DESC "
                "LIMIT %s"
            )
            params: tuple = (channel_id, limit)
        else:
            sql = (
                "SELECT rw_id, rw_channel_id, rw_author_id, rw_client_ref, "
                "       rw_body, rw_is_edited, rw_created_at, rw_edited_at, "
                "       rw_deleted_at, rw_deleted_reason "
                "FROM rw_message "
                "WHERE rw_channel_id = %s "
                "  AND rw_deleted_at IS NULL "
                "  AND (rw_created_at, rw_id) < (%s::timestamptz, %s::uuid) "
                "ORDER BY rw_created_at DESC, rw_id DESC "
                "LIMIT %s"
            )
            params = (
                channel_id,
                before[0],
                before[1],
                limit,
            )
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [self._row_to_message(r) for r in rows]

    def edit(
        self, *, message_id: UUID, editor_id: UUID, new_body: str
    ) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "CALL rw_edit_message(%s, %s, %s)",
                (message_id, editor_id, new_body),
            )
            # Procedures don't return rowcount; verify by re-select.
            cur.execute(
                "SELECT 1 FROM rw_message "
                "WHERE rw_id = %s AND rw_is_edited = true "
                "  AND rw_edited_at >= now() - interval '5 seconds'",
                (message_id,),
            )
            return cur.fetchone() is not None

    def logical_delete(
        self,
        *,
        message_id: UUID,
        actor_id: UUID,
        reason: str,
    ) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "CALL rw_delete_message(%s, %s, %s)",
                (message_id, actor_id, reason),
            )
            cur.execute(
                "SELECT 1 FROM rw_message "
                "WHERE rw_id = %s AND rw_deleted_at IS NOT NULL "
                "  AND rw_deleted_reason = %s",
                (message_id, reason),
            )
            return cur.fetchone() is not None

    def mark_read(self, *, message_id: UUID, user_id: UUID) -> bool:
        # INSERT ... ON CONFLICT DO NOTHING. Returns True iff a new
        # row was inserted (the unique constraint on
        # (rw_message_id, rw_user_id) makes this safe to retry).
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rw_message_read (rw_message_id, rw_user_id) "
                "VALUES (%s, %s) "
                "ON CONFLICT (rw_message_id, rw_user_id) DO NOTHING "
                "RETURNING rw_id",
                (message_id, user_id),
            )
            return cur.fetchone() is not None

    def unread_count_for_channel(
        self, *, channel_id: UUID, user_id: UUID
    ) -> int:
        """Phase 5: thin wrapper around `rw_unread_count_for_channel`.
        Returns 0 for non-members (the DB function enforces it).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_unread_count_for_channel(%s, %s)",
                (channel_id, user_id),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def mark_channel_read(self, *, channel_id: UUID, user_id: UUID) -> int:
        """Phase 5: thin wrapper around `rw_mark_channel_read`.
        Returns the number of rows actually inserted.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_mark_channel_read(%s, %s)",
                (channel_id, user_id),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    @staticmethod
    def _row_to_message(row: tuple) -> Message:
        return Message(
            rw_id=row[0],
            rw_channel_id=row[1],
            rw_author_id=row[2],
            rw_client_ref=row[3],
            rw_body=row[4],
            rw_is_edited=row[5],
            rw_created_at=row[6],
            rw_edited_at=row[7],
            rw_deleted_at=row[8],
            rw_deleted_reason=row[9],
        )


# Backwards-compat alias for the row shape returned by `rw_send_message`.
# The function's first OUT parameter is `was_replay`; the next 10 are
# the rw_message columns in their schema order. The adapter above
# uses this shift implicitly; tests and other callers can use it too.
def _row_to_message_from_send(row10: tuple) -> Message:
    return PostgresMessageRepository._row_to_message(row10)


class PostgresSearchRepository:
    """Phase 5: thin wrapper around `rw_search_messages(...)`.

    The DB function handles everything important (locale pull from
    rw_user, ts_headline, RLS-bypass defense-in-depth membership
    check). This adapter is a one-liner.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def search_in_channel(
        self, *, channel_id: UUID, query: str, limit: int
    ) -> list[SearchHit]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT out_rw_id, out_rw_channel_id, out_rw_author_id, "
                "       out_rw_body, out_rw_created_at, out_rw_highlight "
                "FROM rw_search_messages(%s, %s, %s, "
                "       current_setting('app.current_user_id', true)::uuid)",
                (channel_id, query, limit),
            )
            rows = cur.fetchall()
            return [
                SearchHit(
                    rw_id=r[0],
                    rw_channel_id=r[1],
                    rw_author_id=r[2],
                    rw_body=r[3],
                    rw_created_at=r[4],
                    rw_highlight=r[5],
                )
                for r in rows
            ]


class PostgresRefreshTokenStore:
    """Postgres-backed refresh token store.

    The `revoke_family` method is intentionally a SINGLE SQL UPDATE;
    the test suite asserts this contract (see tests/unit/application/
    auth/test_refresh.py::test_reuse_detection_revokes_entire_family).
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        family_id: UUID,
        expires_at: datetime,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rw_refresh_token "
                "(rw_user_id, rw_token_hash, rw_family_id, rw_expires_at) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, token_hash, family_id, expires_at),
            )

    def find_by_hash(self, token_hash: str) -> RefreshTokenRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_id, rw_user_id, rw_token_hash, rw_family_id, "
                "       rw_expires_at, rw_revoked_at "
                "FROM rw_refresh_token WHERE rw_token_hash = %s",
                (token_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return RefreshTokenRecord(*row)

    def revoke(self, token_id: UUID) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE rw_refresh_token SET rw_revoked_at = now() "
                "WHERE rw_id = %s AND rw_revoked_at IS NULL",
                (token_id,),
            )

    def revoke_family(self, family_id: UUID) -> None:
        # ── Security-critical SQL ────────────────────────────────────
        # Single statement, family-wide, idempotent (only revokes
        # non-revoked rows). If you find yourself "optimizing" this
        # into a Python loop, stop — the family-wide revoke is the
        # whole point of reuse detection.
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE rw_refresh_token SET rw_revoked_at = now() "
                "WHERE rw_family_id = %s AND rw_revoked_at IS NULL",
                (family_id,),
            )


# ─── Plain factory (no pool — single-process dev) ───────────────────────


def make_session_factory(database_url: str) -> SessionFactory:
    """Return a callable that opens a fresh psycopg connection.

    Phase 7 (deployment) replaces this with a real pool; Phase 2 keeps
    it simple — each request opens one connection, runs in one
    transaction, closes.
    """

    def factory() -> Connection:
        return Connection.connect(database_url, autocommit=False)

    return factory


@dataclass(frozen=True)
class _ConnectionFactory:
    url: str

    def __call__(self) -> Connection:
        return Connection.connect(self.url, autocommit=False)
