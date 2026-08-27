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
    ChatProvider,
    ChatUsage,
    CopilotUsageRepository,
    DIRECT,
    EmbeddingProvider,
    GROUP,
    JwtService,
    MEMBER,
    Message,
    MessageEdit,
    MessageRepository,
    PasswordHasher,
    ProviderError,
    RefreshTokenRecord,
    RefreshTokenStore,
    RetrievedChunk,
    SearchHit,
    SearchRepository,
    SessionFactory,
    TransientProviderError,
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

    def search_similar(
        self,
        *,
        actor_id: UUID,
        embedding: list[float],
        limit: int,
    ) -> list[RetrievedChunk]:
        """Phase 6: top-K cosine neighbours from `rw_visible_message`.

        The actor GUC is set by `RwSession` so RLS filters the rows
        to channels the actor is a current member of. A non-member
        gets an empty result set — the same posture as the rest of
        the API. The query is parameterized; no string concatenation.

        The HNSW index on `rw_embedding` (Phase 1, 0030_indexes.sql)
        backs the `<=>` operator. We sort by `distance` ascending
        and apply a keyset-style LIMIT (no OFFSET — AGENTS.md
        prohibited actions).
        """
        # `::vector` is the pgvector literal cast. We render the
        # embedding as a JSON array and let pgvector parse it; this
        # is the documented pattern from the pgvector README.
        vec_lit = "[" + ",".join(repr(float(v)) for v in embedding) + "]"
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT rw_id, rw_channel_id, rw_body, rw_created_at, "
                "       rw_embedding <=> %s::vector AS distance "
                "FROM rw_visible_message "
                "WHERE rw_deleted_at IS NULL "
                "ORDER BY rw_embedding <=> %s::vector "
                "LIMIT %s",
                (vec_lit, vec_lit, limit),
            )
            rows = cur.fetchall()
            return [
                RetrievedChunk(
                    rw_id=r[0],
                    rw_channel_id=r[1],
                    rw_body=r[2],
                    rw_created_at=r[3],
                    # Distance may be NULL if the row's embedding is
                    # NULL (Phase 1 + the trigger only WARN — some
                    # test data may have no embedding). Use a large
                    # sentinel so the row still appears in the LIMIT
                    # (NULL sorts last in ASC, but `ORDER BY x ASC`
                    # skips NULL entirely in some plans). Use 1e9.
                    distance=float(r[4]) if r[4] is not None else 1e9,
                )
                for r in rows
            ]

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


# ─── Phase 6: AI provider adapters + audit repo ─────────────────────────


# Lazy import so the rest of the codebase doesn't pull httpx into the
# sync path until the copilot endpoint is exercised. The mistralai
# SDK is optional too — only MistralAdapter imports it, so a project
# that only runs the chat endpoint can install httpx alone.
try:
    import httpx  # noqa: F401  (used by NvidiaAdapter below)
except ImportError:  # pragma: no cover — httpx is a dev dep already
    httpx = None  # type: ignore[assignment]


class MistralAdapter(EmbeddingProvider):
    """Mistral `mistral-embed` (1024 dims). Free "Experiment" tier.

    Batches up to `BATCH_LIMIT` texts per HTTP call (the Mistral free
    tier is rate-limited per request, not per text — so batching is
    the throughput lever). Three-attempt exponential backoff on
    429 / 5xx.

    Construction requires a real `MISTRAL_API_KEY`. The use case
    refuses to instantiate this adapter when the key is empty;
    `main.py` checks the env var and falls back to a placeholder
    so the rest of the app still boots for development.
    """

    BATCH_LIMIT = 512
    MAX_RETRIES = 3

    def __init__(self, api_key: str, model: str = "mistral-embed") -> None:
        if not api_key:
            raise ProviderError(
                "MISTRAL_API_KEY is empty; cannot construct MistralAdapter"
            )
        # Imported lazily so the SDK isn't required for unit tests
        # that pass FakeEmbeddingProvider.
        from mistralai import Mistral  # type: ignore[import-not-found]

        self._client = Mistral(api_key=api_key)
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_LIMIT):
            chunk = texts[start : start + self.BATCH_LIMIT]
            results.extend(self._embed_chunk_with_retry(chunk))
        return results

    def _embed_chunk_with_retry(self, chunk: list[str]) -> list[list[float]]:
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._client.embeddings.create(
                    model=self._model, inputs=chunk, encoding_format="float"
                )
                # Order-preserving: resp.data[i] corresponds to chunk[i].
                return [d.embedding for d in resp.data]
            except Exception as e:  # mistralai raises SDKError
                last_exc = e
                if attempt == self.MAX_RETRIES - 1:
                    break
                import time as _t

                _t.sleep(2**attempt)
        raise TransientProviderError(
            f"mistral embed failed after {self.MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc


class NvidiaAdapter(ChatProvider):
    """NVIDIA NIM, OpenAI-compatible /chat/completions.

    Synchronous adapter (`httpx.Client`) to match the rest of the
    codebase's sync posture. The FastAPI handler is `async def` but
    delegates to the sync RwSession-based use case anyway; adding an
    async adapter would force async use cases across the whole
    app and Phase 7 introduces a threadpool anyway.

    Three-attempt exponential backoff on 429 / 5xx. The model name
    comes from `Settings.chat_model_primary` (default
    `mistralai/mistral-nemotron`) — never hardcoded here.
    """

    ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
    MAX_RETRIES = 3

    def __init__(
        self,
        api_key: str,
        default_model: str,
        timeout_s: float = 30.0,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "NVIDIA_API_KEY is empty; cannot construct NvidiaAdapter"
            )
        self._api_key = api_key
        self._default_model = default_model
        self._client = httpx.Client(timeout=timeout_s)

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> tuple[str, ChatUsage]:
        payload = {
            "model": model or self._default_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._client.post(
                    self.ENDPOINT, json=payload, headers=headers
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = TransientProviderError(
                        f"nvidia {resp.status_code}: {resp.text[:200]}"
                    )
                elif resp.status_code >= 400:
                    raise ProviderError(
                        f"nvidia {resp.status_code}: {resp.text[:200]}"
                    )
                else:
                    data = resp.json()
                    usage = data.get("usage", {})
                    text = data["choices"][0]["message"]["content"]
                    return text, ChatUsage(
                        prompt_tokens=int(usage.get("prompt_tokens", 0)),
                        completion_tokens=int(
                            usage.get("completion_tokens", 0)
                        ),
                        model=payload["model"],
                    )
            except ProviderError:
                # Permanent — surface immediately, do not retry.
                raise
            except Exception as e:  # network / parse / etc
                last_exc = TransientProviderError(f"nvidia chat error: {e}")

            if attempt < self.MAX_RETRIES - 1:
                import time as _t

                _t.sleep(2**attempt)

        # All retries exhausted on a transient error.
        raise last_exc or ProviderError("nvidia chat failed (no detail)")

    def close(self) -> None:
        self._client.close()


class PostgresCopilotUsageRepository(CopilotUsageRepository):
    """Persist `rw_copilot_usage` rows (Phase 6, §11.4).

    `record(...)` is the unconditional audit hook the use case
    invokes on every copilot call — success or failure, tokens or
    zero tokens. The §11.4 report groups by `rw_user_id` to spot
    abuse / runaway tokens.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(
        self,
        *,
        actor_id: UUID,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rw_copilot_usage "
                "(rw_user_id, rw_model, rw_prompt_tokens, rw_completion_tokens) "
                "VALUES (%s, %s, %s, %s)",
                (actor_id, model, prompt_tokens, completion_tokens),
            )


@dataclass(frozen=True)
class CopilotUsageSummary:
    """Aggregated §11.4 view (per-user). Returned by the
    `GET /api/v1/copilot/usage` endpoint."""

    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: float


def fetch_copilot_usage_summary(
    conn: Connection, *, actor_id: UUID
) -> CopilotUsageSummary:
    """`SELECT count(*), sum(...) FROM rw_copilot_usage WHERE rw_user_id=...`.

    RLS does not apply (the policy on `rw_copilot_usage` is restrictive
    enough that the actor can only see their own rows anyway), but
    we still parameterize — defence in depth.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), "
            "       COALESCE(sum(rw_prompt_tokens), 0), "
            "       COALESCE(sum(rw_completion_tokens), 0), "
            "       COALESCE(sum(rw_cost_usd), 0) "
            "FROM rw_copilot_usage WHERE rw_user_id = %s",
            (actor_id,),
        )
        row = cur.fetchone()
        return CopilotUsageSummary(
            total_calls=int(row[0] or 0),
            total_prompt_tokens=int(row[1] or 0),
            total_completion_tokens=int(row[2] or 0),
            total_cost_usd=float(row[3] or 0.0),
        )
