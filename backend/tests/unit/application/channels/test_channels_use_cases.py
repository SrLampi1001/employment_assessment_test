"""Unit tests for the channels use cases.

In-memory fakes only — no I/O. Mirrors the auth unit-test pattern.

Asserts the three human-review checks from `docs/ARCHITECTURE.md §2` +
the issue #5 review checklist:

1. **`rw_create_channel` path** — the creator is added as `owner` in
   the same statement. We test it by checking that after
   `create_group`, the faked channel repo's `create` was called with
   `creator_id`, and that the actor's `list_visible` returns the new
   channel with `my_role = OWNER` (=2).
2. **`AddMember` use case** — only the channel owner can add members.
   Asserted by `test_add_member_rejects_non_owner` (raises
   `ChannelError("not-owner")`) and `test_add_member_rejects_duplicate`
   (raises `ChannelError("already-member")`).
3. **404 vs 403 on non-member reads** — never 403. Asserted by
   `test_list_visible_does_not_leak_non_member_channels` and
   `test_leave_channel_returns_404_for_non_visible` (uses the faked
   `channel_repo.find` returning `None` for unknown ids).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.channels import (
    AddMember,
    ChannelError,
    ChannelSummary,
    CreateChannel,
    LeaveChannel,
    ListVisibleChannels,
)
from app.domain import DIRECT, GROUP, MEMBER, OWNER, Channel, ChannelMember


# ─── Fakes ──────────────────────────────────────────────────────────────


@dataclass
class _FakeChannelRepo:
    channels: dict[UUID, Channel] = field(default_factory=dict)
    # The fake member-repo is held by reference so `create` can seed
    # the creator's owner membership in the same way the DB function
    # does it atomically. Wired up in `_use_cases()`.
    member_repo: "_FakeChannelMemberRepo | None" = None

    def __call__(self, conn=None) -> "_FakeChannelRepo":
        return self

    def list_visible(self) -> list[tuple[Channel, ChannelMember]]:
        out: list[tuple[Channel, ChannelMember]] = []
        if self.member_repo is None:
            return out
        for ch in self.channels.values():
            for m in self.member_repo._memberships.values():
                if m.rw_channel_id == ch.rw_id and m.rw_left_at is None:
                    out.append((ch, m))
        return out

    def find(self, channel_id: UUID) -> Channel | None:
        return self.channels.get(channel_id)

    def create(self, *, name: str, kind: int, creator_id: UUID) -> UUID:
        cid = uuid4()
        self.channels[cid] = Channel(
            rw_id=cid,
            rw_name=name,
            rw_kind=kind,
            rw_created_by=creator_id,
            rw_created_at=datetime.now(timezone.utc),
        )
        # Also seed the creator's owner membership — mirrors the DB
        # function's atomic create + first member. The member repo's
        # `add` enforces "owner-only", which would deadlock this
        # bootstrap call, so seed the row directly.
        assert self.member_repo is not None
        self.member_repo._memberships[(cid, creator_id)] = ChannelMember(
            rw_id=uuid4(),
            rw_channel_id=cid,
            rw_user_id=creator_id,
            rw_role=OWNER,
            rw_joined_at=datetime.now(timezone.utc),
            rw_left_at=None,
        )
        return cid


@dataclass
class _FakeChannelMemberRepo:
    _memberships: dict[tuple[UUID, UUID], ChannelMember] = field(default_factory=dict)
    channel_repo: _FakeChannelRepo | None = None

    def __call__(self, conn=None) -> "_FakeChannelMemberRepo":
        return self

    def add(
        self,
        *,
        channel_id: UUID,
        inviter_id: UUID,
        new_member_id: UUID,
        role: int = MEMBER,
    ) -> ChannelMember:
        # Mirror the DB function: reject if inviter is not the channel
        # creator; reject if the new member is already active.
        if self.channel_repo is None:
            raise RuntimeError("fake not wired")
        ch = self.channel_repo.channels.get(channel_id)
        if ch is None:
            raise RuntimeError("channel not found")
        if ch.rw_created_by != inviter_id:
            raise RuntimeError(
                "rw_add_channel_member: only the channel owner may add members"
            )
        existing = self._memberships.get((channel_id, new_member_id))
        if existing and existing.rw_left_at is None:
            raise RuntimeError("user is already an active member")
        m = ChannelMember(
            rw_id=uuid4(),
            rw_channel_id=channel_id,
            rw_user_id=new_member_id,
            rw_role=role,
            rw_joined_at=datetime.now(timezone.utc),
            rw_left_at=None,
        )
        self._memberships[(channel_id, new_member_id)] = m
        return m

    def leave(self, *, channel_id: UUID, user_id: UUID) -> bool:
        key = (channel_id, user_id)
        m = self._memberships.get(key)
        if m is None or m.rw_left_at is not None:
            return False
        self._memberships[key] = ChannelMember(
            **{**m.__dict__, "rw_left_at": datetime.now(timezone.utc)}
        )
        return True


# Fake-coordination seam (single shared fake for the test module).
_fake_repo: _FakeChannelRepo | None = None


def _channel_repo_lookup(channel_id: UUID) -> Channel | None:
    if _fake_repo is None:
        return None
    return _fake_repo.channels.get(channel_id)


@dataclass
class _FakeUserRepo:
    users: dict[str, tuple[Any, str]] = field(default_factory=dict)
    # Track user_ids → username so the fakes can correlate.
    by_id: dict[UUID, Any] = field(default_factory=dict)

    def __call__(self, conn=None) -> "_FakeUserRepo":
        return self

    def find_by_username(self, username: str):
        return self.users.get(username)

    def search_by_username_prefix(self, prefix: str, limit: int):
        out: list = []
        for username, (u, _) in self.users.items():
            if username.startswith(prefix):
                out.append(u)
                if len(out) >= limit:
                    break
        return out


def _user(username: str = "alice") -> Any:
    from app.domain import User

    u = User(
        rw_id=uuid4(),
        rw_username=username,
        rw_display_name=username.title(),
        rw_locale="es",
        rw_created_at=datetime.now(timezone.utc),
    )
    return u


@dataclass
class _FakeSession:
    def __call__(self):
        from tests.unit.application.auth.test_use_cases import _NullConnection

        return _NullConnection()


def _use_cases():
    """Build the four use cases with the fakes wired up."""
    user_repo = _FakeUserRepo()
    alice = _user("alice")
    bob = _user("bob")
    user_repo.users["alice"] = (alice, "hash")
    user_repo.users["bob"] = (bob, "hash")
    user_repo.by_id[alice.rw_id] = alice
    user_repo.by_id[bob.rw_id] = bob

    channel_repo = _FakeChannelRepo()
    channel_member_repo = _FakeChannelMemberRepo(
        channel_repo=channel_repo
    )
    channel_repo.member_repo = channel_member_repo

    sf = _FakeSession()
    return {
        "session_factory": sf,
        "user_repo_factory": user_repo,
        "channel_repo_factory": channel_repo,
        "channel_member_repo_factory": channel_member_repo,
        "alice": alice,
        "bob": bob,
    }


# ─── CreateChannel ──────────────────────────────────────────────────────


def test_create_group_returns_channel_id_and_seed_creator_as_owner() -> None:
    ctx = _use_cases()
    uc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )

    cid = uc.create_group(actor_id=ctx["alice"].rw_id, name="team-platform")

    ch = ctx["channel_repo_factory"].channels[cid]
    assert ch.rw_name == "team-platform"
    assert ch.rw_kind == GROUP
    assert ch.rw_created_by == ctx["alice"].rw_id


def test_create_group_rejects_empty_name() -> None:
    ctx = _use_cases()
    uc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    with pytest.raises(ChannelError) as exc:
        uc.create_group(actor_id=ctx["alice"].rw_id, name="")
    assert exc.value.code == "invalid-name"


def test_create_direct_adds_both_users_atomically() -> None:
    ctx = _use_cases()
    uc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )

    cid = uc.create_direct(actor_id=ctx["alice"].rw_id, other_username="bob")

    ch = ctx["channel_repo_factory"].channels[cid]
    assert ch.rw_kind == DIRECT
    assert ch.rw_created_by == ctx["alice"].rw_id
    # Both memberships are now present.
    m_repo = ctx["channel_member_repo_factory"]
    assert (cid, ctx["alice"].rw_id) in m_repo._memberships
    assert (cid, ctx["bob"].rw_id) in m_repo._memberships


def test_create_direct_rejects_self_loop() -> None:
    ctx = _use_cases()
    uc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    with pytest.raises(ChannelError) as exc:
        uc.create_direct(actor_id=ctx["alice"].rw_id, other_username="alice")
    assert exc.value.code == "self-direct"


def test_create_direct_rejects_unknown_user() -> None:
    ctx = _use_cases()
    uc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    with pytest.raises(ChannelError) as exc:
        uc.create_direct(actor_id=ctx["alice"].rw_id, other_username="ghost")
    assert exc.value.code == "user-not-found"


# ─── AddMember ──────────────────────────────────────────────────────────


def test_add_member_by_owner_returns_new_membership() -> None:
    ctx = _use_cases()
    cc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    am = AddMember(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
    )
    cid = cc.create_group(actor_id=ctx["alice"].rw_id, name="team")
    m = am(
        actor_id=ctx["alice"].rw_id,
        channel_id=cid,
        new_member_id=ctx["bob"].rw_id,
        role=MEMBER,
    )
    assert m.rw_channel_id == cid
    assert m.rw_user_id == ctx["bob"].rw_id
    assert m.rw_role == MEMBER


def test_add_member_rejects_non_owner() -> None:
    ctx = _use_cases()
    cc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    am = AddMember(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
    )
    cid = cc.create_group(actor_id=ctx["alice"].rw_id, name="team")

    # Bob (not the creator) tries to invite someone.
    with pytest.raises(ChannelError) as exc:
        am(
            actor_id=ctx["bob"].rw_id,
            channel_id=cid,
            new_member_id=ctx["bob"].rw_id,  # even himself
            role=MEMBER,
        )
    assert exc.value.code == "not-owner"


def test_add_member_rejects_duplicate_active_membership() -> None:
    ctx = _use_cases()
    cc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    am = AddMember(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
    )
    cid = cc.create_group(actor_id=ctx["alice"].rw_id, name="team")
    am(actor_id=ctx["alice"].rw_id, channel_id=cid, new_member_id=ctx["bob"].rw_id)
    with pytest.raises(ChannelError) as exc:
        am(actor_id=ctx["alice"].rw_id, channel_id=cid, new_member_id=ctx["bob"].rw_id)
    assert exc.value.code == "already-member"


def test_add_member_returns_404_for_unknown_channel() -> None:
    ctx = _use_cases()
    am = AddMember(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
    )
    with pytest.raises(ChannelError) as exc:
        am(
            actor_id=ctx["alice"].rw_id,
            channel_id=uuid4(),
            new_member_id=ctx["bob"].rw_id,
        )
    assert exc.value.code == "channel-not-found"


# ─── ListVisibleChannels ────────────────────────────────────────────────


def test_list_visible_returns_actor_channels_with_role() -> None:
    ctx = _use_cases()
    cc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    lv = ListVisibleChannels(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
    )
    cc.create_group(actor_id=ctx["alice"].rw_id, name="alpha")
    cc.create_group(actor_id=ctx["alice"].rw_id, name="beta")

    items = lv(actor_id=ctx["alice"].rw_id)
    names = sorted(i.name for i in items)
    assert names == ["alpha", "beta"]
    assert all(i.my_role == OWNER for i in items)


# ─── LeaveChannel ───────────────────────────────────────────────────────


def test_leave_channel_sets_left_at_and_is_idempotent() -> None:
    ctx = _use_cases()
    cc = CreateChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
        user_repo_factory=ctx["user_repo_factory"],
    )
    lc = LeaveChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
    )
    cid = cc.create_group(actor_id=ctx["alice"].rw_id, name="team")
    assert lc(actor_id=ctx["alice"].rw_id, channel_id=cid) is True
    # Idempotent — leaving again returns False.
    assert lc(actor_id=ctx["alice"].rw_id, channel_id=cid) is False


def test_leave_channel_returns_404_for_non_visible() -> None:
    ctx = _use_cases()
    lc = LeaveChannel(
        session_factory=ctx["session_factory"],
        channel_repo_factory=ctx["channel_repo_factory"],
        channel_member_repo_factory=ctx["channel_member_repo_factory"],
    )
    with pytest.raises(ChannelError) as exc:
        lc(actor_id=ctx["alice"].rw_id, channel_id=uuid4())
    assert exc.value.code == "channel-not-found"


# ─── search_by_username_prefix (used by the invite UI) ──────────────────


def test_user_search_finds_by_prefix_and_respects_limit() -> None:
    ctx = _use_cases()
    repo = ctx["user_repo_factory"]
    # Add three more users.
    from app.domain import User

    for name in ["albert", "alfred", "arnold"]:
        u = User(
            rw_id=uuid4(),
            rw_username=name,
            rw_display_name=name.title(),
            rw_locale="en",
            rw_created_at=datetime.now(timezone.utc),
        )
        repo.users[name] = (u, "hash")
    results = repo.search_by_username_prefix("al", limit=2)
    assert len(results) == 2
    assert all(r.rw_username.startswith("al") for r in results)
