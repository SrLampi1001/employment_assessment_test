"""Channels use cases (Create / AddMember / ListVisible / Leave).

Per ARCHITECTURE.md §5.1, use cases are thin: validate input, dispatch
to a port (or DB function), and map results.

The three human-review checks from issue #5 (DECISIONS.md):

1. **`rw_create_channel` path** — verify the creator is added as
   `owner` in the same statement. Asserted by the unit test
   `test_create_channel_atomic_owner_membership` + the BDD scenario
   `Creator sees the new channel`.
2. **`AddMember` use case** — verify only the channel owner can add
   members. Enforced by the `rw_add_channel_member` SECURITY DEFINER
   function (Phase 3 migration `0100`); the function raises if the
   inviter is not the channel creator. Asserted by the unit test
   `test_add_member_rejects_non_owner`.
3. **404 vs 403 on non-member reads** — never `403`. Per
   `ARCHITECTURE.md §6`, missing-or-invisible resources return 404 so
   a non-member can't probe whether a channel exists. Asserted by the
   BDD scenario `Non-member gets 404 from any /channels/{id}/* endpoint`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from .domain import (
    DIRECT,
    GROUP,
    Channel,
    ChannelMember,
    ChannelMemberRepository,
    ChannelRepository,
    MEMBER,
    OWNER,
    SessionFactory,
    UserRepository,
)
from .infrastructure import RwSession


# ─── Errors ─────────────────────────────────────────────────────────────


class ChannelError(Exception):
    """Any channel-flow failure. The `code` field drives the HTTP
    status in `app.delivery_channels._status_for`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ChannelSummary:
    """Returned by `ListVisibleChannels` — channel + the actor's role."""

    channel_id: UUID
    name: str
    kind: int
    created_by: UUID
    created_at: datetime
    my_role: int
    # Phase 5: unread count for the actor in this channel. 0 for
    # channels where everything is read. 0 for non-members (but
    # non-members are filtered out by RLS before this dataclass
    # is built).
    unread_count: int = 0


# Factory types — class objects work as factories, same pattern as Phase 2.
ChannelRepoFactory = Callable[..., ChannelRepository]
ChannelMemberRepoFactory = Callable[..., ChannelMemberRepository]
UserRepoFactoryForChannels = Callable[..., UserRepository]


# ─── CreateChannel ──────────────────────────────────────────────────────


class CreateChannel:
    """Create a channel + the creator's owner membership atomically.

    Two entry points:

    - `create_group(...)` — name + kind=group; the creator is the only
      member; more members join via `AddMember`.
    - `create_direct(...)` — other_username; resolves the user, derives
      a canonical name so the same pair always collides on the same
      channel (Phase 3 doesn't enforce uniqueness; a follow-up adds a
      unique index), and adds both users as members.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        channel_repo_factory: ChannelRepoFactory,
        channel_member_repo_factory: ChannelMemberRepoFactory,
        user_repo_factory: UserRepoFactoryForChannels,
    ) -> None:
        self._session_factory = session_factory
        self._channel_repo_factory = channel_repo_factory
        self._channel_member_repo_factory = channel_member_repo_factory
        self._user_repo_factory = user_repo_factory

    def create_group(self, *, actor_id: UUID, name: str) -> UUID:
        if not (1 <= len(name) <= 120):
            raise ChannelError("invalid-name", "name length must be 1..120")

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            channel_repo = self._channel_repo_factory(conn)
            return channel_repo.create(name=name, kind=GROUP, creator_id=actor_id)

    def create_direct(self, *, actor_id: UUID, other_username: str) -> UUID:
        """Create a direct channel between the actor and `other_username`.

        Atomic at the application layer: rw_create_channel adds the
        creator, then rw_add_channel_member adds the other user.
        The two statements live in the same RwSession transaction; if
        the second fails the first rolls back.
        """
        if not (1 <= len(other_username) <= 64):
            raise ChannelError("invalid-username", "username length must be 1..64")

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            user_repo = self._user_repo_factory(conn)
            channel_repo = self._channel_repo_factory(conn)
            member_repo = self._channel_member_repo_factory(conn)

            other_with_hash = user_repo.find_by_username(other_username)
            if other_with_hash is None:
                raise ChannelError(
                    "user-not-found", f"user {other_username!r} does not exist"
                )
            other, _ = other_with_hash
            if other.rw_id == actor_id:
                raise ChannelError(
                    "self-direct",
                    "cannot create a direct channel with yourself",
                )

            a, b = sorted([str(actor_id), str(other.rw_id)])
            name = f"direct::{a}::{b}"
            channel_id = channel_repo.create(
                name=name, kind=DIRECT, creator_id=actor_id
            )
            member_repo.add(
                channel_id=channel_id,
                inviter_id=actor_id,
                new_member_id=other.rw_id,
                role=MEMBER,
            )
            return channel_id


# ─── AddMember ──────────────────────────────────────────────────────────


class AddMember:
    """Channel owner invites another user.

    Authorization is enforced inside `rw_add_channel_member` (SECURITY
    DEFINER). The use case is a thin dispatcher — it surfaces the
    function's exceptions as typed `ChannelError`s.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        channel_repo_factory: ChannelRepoFactory,
        channel_member_repo_factory: ChannelMemberRepoFactory,
    ) -> None:
        self._session_factory = session_factory
        self._channel_repo_factory = channel_repo_factory
        self._member_repo_factory = channel_member_repo_factory

    def __call__(
        self,
        *,
        actor_id: UUID,
        channel_id: UUID,
        new_member_id: UUID,
        role: int = MEMBER,
    ) -> ChannelMember:
        if role not in (MEMBER, OWNER):
            raise ChannelError("invalid-role", "role must be 1 (member) or 2 (owner)")

        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            channel_repo = self._channel_repo_factory(conn)
            member_repo = self._member_repo_factory(conn)

            channel = channel_repo.find(channel_id)
            if channel is None:
                # 404 — never 403, per ARCHITECTURE.md §6.
                raise ChannelError("channel-not-found", "channel not found")

            try:
                return member_repo.add(
                    channel_id=channel_id,
                    inviter_id=actor_id,
                    new_member_id=new_member_id,
                    role=role,
                )
            except Exception as exc:
                msg = str(exc)
                if "owner" in msg and "add members" in msg:
                    raise ChannelError(
                        "not-owner",
                        "only the channel owner may add members",
                    ) from exc
                if "already an active member" in msg:
                    raise ChannelError(
                        "already-member",
                        "user is already an active member of this channel",
                    ) from exc
                raise


# ─── ListVisibleChannels ────────────────────────────────────────────────


class ListVisibleChannels:
    """The actor's visible channels + their role in each.

    RLS gates the underlying `rw_channel` SELECT — non-member rows are
    invisible, so the result is automatically scoped. The application
    layer just sorts + projects.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        channel_repo_factory: ChannelRepoFactory,
    ) -> None:
        self._session_factory = session_factory
        self._channel_repo_factory = channel_repo_factory

    def __call__(self, *, actor_id: UUID) -> list[ChannelSummary]:
        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            repo = self._channel_repo_factory(conn)
            rows = repo.list_visible_with_unread()
            return [
                ChannelSummary(
                    channel_id=ch.rw_id,
                    name=ch.rw_name,
                    kind=ch.rw_kind,
                    created_by=ch.rw_created_by,
                    created_at=ch.rw_created_at,
                    my_role=m.rw_role,
                    unread_count=unread,
                )
                for ch, m, unread in rows
            ]


# ─── LeaveChannel ───────────────────────────────────────────────────────


class LeaveChannel:
    """Sets `rw_left_at = now()` on the actor's current membership.

    Idempotent: leaving a channel you're not a member of is a no-op
    (returns `False`). After this call, the actor immediately stops
    seeing the channel in `ListVisibleChannels` — RLS does the work.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        channel_repo_factory: ChannelRepoFactory,
        channel_member_repo_factory: ChannelMemberRepoFactory,
    ) -> None:
        self._session_factory = session_factory
        self._channel_repo_factory = channel_repo_factory
        self._member_repo_factory = channel_member_repo_factory

    def __call__(self, *, actor_id: UUID, channel_id: UUID) -> bool:
        with RwSession(self._session_factory, actor_id=actor_id) as conn:
            channel_repo = self._channel_repo_factory(conn)
            member_repo = self._member_repo_factory(conn)
            channel = channel_repo.find(channel_id)
            if channel is None:
                # 404 — never 403, per ARCHITECTURE.md §6.
                raise ChannelError("channel-not-found", "channel not found")
            return member_repo.leave(channel_id=channel_id, user_id=actor_id)
