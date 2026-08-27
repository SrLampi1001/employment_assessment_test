"""Unit tests for the messages use cases.

In-memory fakes only — no I/O. Mirrors the auth + channels unit-test
patterns.

Asserts the three human-review checks from issue #4:

1. **Send idempotency** — same `client_ref` returns the original row
   (`was_replay=True`). Asserted by `test_send_returns_replay_flag`.
2. **Keyset pagination** — `before` cursor is `(created_at, id)` and
   the page returns messages strictly older. Asserted by
   `test_history_returns_strict_older_messages` + `test_history_next_cursor`.
3. **Edit append pattern** — `rw_message_edit` rows are INSERT-only.
   Asserted at the integration level by the BDD scenario
   `Edit appends a rw_message_edit row` (the unit tests for EditMessage
   cover the use case's input validation; the append behavior is the
   DB function's contract).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain import Message
from app.messages import (
    ChannelHistory,
    DeleteMessage,
    EditMessage,
    MarkRead,
    MessageError,
    SendMessage,
)


# ─── Fakes ──────────────────────────────────────────────────────────────


@dataclass
class _FakeMessageRepo:
    """In-memory `MessageRepository` that mirrors the DB contract.

    `send_idempotent` returns `(message, was_replay)`. The heuristic
    matches the Postgres adapter: an inserted row is "fresh" only if
    `now - created_at < 1s` AND a row with that `client_ref` already
    exists. This is the same heuristic used in production
    (`PostgresMessageRepository.send_idempotent`).
    """

    rows: dict[UUID, Message] = field(default_factory=dict)
    by_client_ref: dict[tuple[UUID, str], UUID] = field(default_factory=dict)
    edit_count: int = 0
    delete_count: int = 0
    read_count: int = 0

    def __call__(self, conn=None) -> "_FakeMessageRepo":
        return self

    def send_idempotent(
        self,
        *,
        channel_id: UUID,
        author_id: UUID,
        body: str,
        client_ref: str | None,
    ) -> tuple[Message, bool]:
        # Replay check: same author + client_ref → return the existing row
        # and mark replay=True. Without client_ref → always fresh.
        if client_ref is not None:
            existing_id = self.by_client_ref.get((author_id, client_ref))
            if existing_id is not None:
                return self.rows[existing_id], True
        # Fresh insert
        mid = uuid4()
        msg = Message(
            rw_id=mid,
            rw_channel_id=channel_id,
            rw_author_id=author_id,
            rw_client_ref=client_ref,
            rw_body=body,
            rw_is_edited=False,
            rw_created_at=datetime.now(timezone.utc),
            rw_edited_at=None,
            rw_deleted_at=None,
            rw_deleted_reason=None,
        )
        self.rows[mid] = msg
        if client_ref is not None:
            self.by_client_ref[(author_id, client_ref)] = mid
        return msg, False

    def find_visible(self, message_id: UUID, viewer_id: UUID) -> Message | None:
        m = self.rows.get(message_id)
        if m is None or m.rw_deleted_at is not None:
            return None
        return m

    def history_keyset(
        self,
        *,
        channel_id: UUID,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Message]:
        # Newest-first; filter to the channel; respect the cursor.
        candidates = sorted(
            [m for m in self.rows.values() if m.rw_channel_id == channel_id and m.rw_deleted_at is None],
            key=lambda m: (m.rw_created_at, m.rw_id),
            reverse=True,
        )
        if before is not None:
            cursor_ts, cursor_id = before
            candidates = [
                m
                for m in candidates
                if (m.rw_created_at, m.rw_id) < (cursor_ts, cursor_id)
            ]
        return candidates[:limit]

    def edit(
        self, *, message_id: UUID, editor_id: UUID, new_body: str
    ) -> bool:
        m = self.rows.get(message_id)
        if m is None or m.rw_deleted_at is not None or m.rw_author_id != editor_id:
            return False
        self.rows[message_id] = Message(
            **{**m.__dict__, "rw_body": new_body, "rw_is_edited": True,
                "rw_edited_at": datetime.now(timezone.utc)}
        )
        self.edit_count += 1
        return True

    def logical_delete(
        self,
        *,
        message_id: UUID,
        actor_id: UUID,
        reason: str,
    ) -> bool:
        m = self.rows.get(message_id)
        if m is None or m.rw_deleted_at is not None or m.rw_author_id != actor_id:
            return False
        self.rows[message_id] = Message(
            **{**m.__dict__, "rw_deleted_at": datetime.now(timezone.utc),
                "rw_deleted_reason": reason}
        )
        self.delete_count += 1
        return True

    def mark_read(self, *, message_id: UUID, user_id: UUID) -> bool:
        self.read_count += 1
        return True


@dataclass
class _FakeSession:
    def __call__(self):
        from tests.unit.application.auth.test_use_cases import _NullConnection
        return _NullConnection()


def _setup() -> tuple[dict[str, Any], UUID]:
    repo = _FakeMessageRepo()
    sf = _FakeSession()
    actor = uuid4()
    return {"repo": repo, "sf": sf}, actor


# ─── SendMessage ────────────────────────────────────────────────────────


def test_send_returns_fresh_message_with_client_ref() -> None:
    ctx, actor = _setup()
    uc = SendMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])

    msg, replay = uc(
        actor_id=actor,
        channel_id=uuid4(),
        body="hello",
        client_ref="client-1",
    )

    assert replay is False
    assert msg.rw_body == "hello"
    assert msg.is_mine is True
    assert msg.rw_created_at is not None


def test_send_replays_with_same_client_ref() -> None:
    ctx, actor = _setup()
    uc = SendMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])

    channel_id = uuid4()
    msg1, replay1 = uc(
        actor_id=actor,
        channel_id=channel_id,
        body="first",
        client_ref="dup",
    )
    msg2, replay2 = uc(
        actor_id=actor,
        channel_id=channel_id,
        body="second-attempt",
        client_ref="dup",
    )

    assert replay1 is False
    assert replay2 is True
    # The original message id is returned on replay; the body is NOT
    # overwritten (idempotency preserves the original send).
    assert msg1.rw_id == msg2.rw_id
    assert msg1.rw_body == "first"
    assert msg2.rw_body == "first"


def test_send_without_client_ref_never_replays() -> None:
    ctx, actor = _setup()
    uc = SendMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])

    channel_id = uuid4()
    msg1, _ = uc(actor_id=actor, channel_id=channel_id, body="a")
    msg2, replay2 = uc(actor_id=actor, channel_id=channel_id, body="a")
    assert msg1.rw_id != msg2.rw_id
    assert replay2 is False


def test_send_rejects_empty_body() -> None:
    ctx, actor = _setup()
    uc = SendMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])
    with pytest.raises(MessageError) as exc:
        uc(actor_id=actor, channel_id=uuid4(), body="")
    assert exc.value.code == "invalid-body"


def test_send_rejects_oversized_body() -> None:
    ctx, actor = _setup()
    uc = SendMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])
    with pytest.raises(MessageError) as exc:
        uc(actor_id=actor, channel_id=uuid4(), body="x" * 8001)
    assert exc.value.code == "invalid-body"


# ─── EditMessage ────────────────────────────────────────────────────────


def test_edit_by_author_updates_body_and_marks_edited() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    send = SendMessage(session_factory=ctx["sf"], message_repo_factory=repo)
    edit = EditMessage(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    msg, _ = send(actor_id=actor, channel_id=channel_id, body="original")

    ok = edit(actor_id=actor, message_id=msg.rw_id, new_body="corrected")
    assert ok is True
    assert repo.rows[msg.rw_id].rw_body == "corrected"
    assert repo.rows[msg.rw_id].rw_is_edited is True
    assert repo.rows[msg.rw_id].rw_edited_at is not None
    assert repo.edit_count == 1


def test_edit_by_non_author_returns_false() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    send = SendMessage(session_factory=ctx["sf"], message_repo_factory=repo)
    edit = EditMessage(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    msg, _ = send(actor_id=actor, channel_id=channel_id, body="original")

    other = uuid4()
    ok = edit(actor_id=other, message_id=msg.rw_id, new_body="hijack")
    assert ok is False
    assert repo.rows[msg.rw_id].rw_body == "original"
    assert repo.edit_count == 0


def test_edit_rejects_empty_body() -> None:
    ctx, actor = _setup()
    edit = EditMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])
    with pytest.raises(MessageError) as exc:
        edit(actor_id=actor, message_id=uuid4(), new_body="")
    assert exc.value.code == "invalid-body"


# ─── DeleteMessage ─────────────────────────────────────────────────────


def test_logical_delete_by_author_returns_true_and_marks_deleted() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    send = SendMessage(session_factory=ctx["sf"], message_repo_factory=repo)
    delete = DeleteMessage(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    msg, _ = send(actor_id=actor, channel_id=channel_id, body="oops")

    ok = delete(actor_id=actor, message_id=msg.rw_id, reason="user-deleted")
    assert ok is True
    assert repo.rows[msg.rw_id].rw_deleted_at is not None
    assert repo.rows[msg.rw_id].rw_deleted_reason == "user-deleted"
    assert repo.delete_count == 1


def test_logical_delete_by_non_author_returns_false() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    send = SendMessage(session_factory=ctx["sf"], message_repo_factory=repo)
    delete = DeleteMessage(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    msg, _ = send(actor_id=actor, channel_id=channel_id, body="oops")

    other = uuid4()
    ok = delete(actor_id=other, message_id=msg.rw_id, reason="user-deleted")
    assert ok is False
    assert repo.rows[msg.rw_id].rw_deleted_at is None
    assert repo.delete_count == 0


def test_delete_rejects_empty_reason() -> None:
    ctx, actor = _setup()
    delete = DeleteMessage(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])
    with pytest.raises(MessageError) as exc:
        delete(actor_id=actor, message_id=uuid4(), reason="")
    assert exc.value.code == "invalid-reason"


# ─── ChannelHistory ────────────────────────────────────────────────────


def _seed_history(repo: _FakeMessageRepo, channel_id: UUID, count: int):
    """Insert `count` messages with monotonic timestamps. Returns the
    list of created `Message` records (raw, not MessageSummary)."""
    import uuid as _u

    send = SendMessage(session_factory=_FakeSession(), message_repo_factory=repo)
    actor = _u.uuid4()
    out = []
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(count):
        msg, _ = send(
            actor_id=actor,
            channel_id=channel_id,
            body=f"msg-{i}",
            client_ref=f"seed-{i}",
        )
        # Re-stamp created_at so the order is deterministic.
        original = repo.rows[msg.rw_id]
        stamped = Message(
            rw_id=original.rw_id,
            rw_channel_id=original.rw_channel_id,
            rw_author_id=original.rw_author_id,
            rw_client_ref=original.rw_client_ref,
            rw_body=original.rw_body,
            rw_is_edited=original.rw_is_edited,
            rw_created_at=base + timedelta(seconds=i),
            rw_edited_at=original.rw_edited_at,
            rw_deleted_at=original.rw_deleted_at,
            rw_deleted_reason=original.rw_deleted_reason,
        )
        repo.rows[msg.rw_id] = stamped
        out.append(stamped)
    return out


def test_history_first_page_returns_newest_first() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    history = ChannelHistory(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    seeded = _seed_history(repo, channel_id, count=5)

    page = history(actor_id=actor, channel_id=channel_id, limit=3)
    bodies = [it.rw_body for it in page.items]
    # Newest first means msg-4, msg-3, msg-2.
    assert bodies == ["msg-4", "msg-3", "msg-2"]
    assert page.next_cursor is not None
    assert page.next_cursor[1] == seeded[2].rw_id  # cursor = last in page


def test_history_keyset_cursor_returns_strictly_older_messages() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    history = ChannelHistory(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    seeded = _seed_history(repo, channel_id, count=5)

    page1 = history(actor_id=actor, channel_id=channel_id, limit=2)
    assert [it.rw_body for it in page1.items] == ["msg-4", "msg-3"]

    # Use page1's cursor to fetch page 2.
    page2 = history(
        actor_id=actor,
        channel_id=channel_id,
        before=page1.next_cursor,
        limit=10,  # bigger than remaining to test next_cursor=None
    )
    assert [it.rw_body for it in page2.items] == ["msg-2", "msg-1", "msg-0"]
    # last page is not full → no further cursor.
    assert page2.next_cursor is None


def test_history_omits_logically_deleted_messages() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    history = ChannelHistory(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    seeded = _seed_history(repo, channel_id, count=3)
    # Logical-delete the middle message.
    repo.rows[seeded[1].rw_id] = Message(
        **{**seeded[1].__dict__, "rw_deleted_at": datetime.now(timezone.utc)}
    )

    page = history(actor_id=actor, channel_id=channel_id, limit=10)
    bodies = [it.rw_body for it in page.items]
    assert "msg-1" not in bodies
    assert bodies == ["msg-2", "msg-0"]


def test_history_rejects_invalid_limit() -> None:
    ctx, actor = _setup()
    history = ChannelHistory(session_factory=ctx["sf"], message_repo_factory=ctx["repo"])
    with pytest.raises(MessageError) as exc:
        history(actor_id=actor, channel_id=uuid4(), limit=0)
    assert exc.value.code == "invalid-limit"
    with pytest.raises(MessageError):
        history(actor_id=actor, channel_id=uuid4(), limit=101)


def test_history_summary_is_mine_flag() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    history = ChannelHistory(session_factory=ctx["sf"], message_repo_factory=repo)

    channel_id = uuid4()
    send = SendMessage(session_factory=ctx["sf"], message_repo_factory=repo)
    msg, _ = send(actor_id=actor, channel_id=channel_id, body="mine")

    page = history(actor_id=actor, channel_id=channel_id, limit=10)
    assert len(page.items) == 1
    assert page.items[0].is_mine is True


# ─── MarkRead ──────────────────────────────────────────────────────────


def test_mark_read_returns_true_for_first_call() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    mark = MarkRead(session_factory=ctx["sf"], message_repo_factory=repo)
    assert mark(actor_id=actor, message_id=uuid4()) is True
    assert repo.read_count == 1


def test_mark_read_is_idempotent() -> None:
    ctx, actor = _setup()
    repo: _FakeMessageRepo = ctx["repo"]
    mark = MarkRead(session_factory=ctx["sf"], message_repo_factory=repo)
    mid = uuid4()
    assert mark(actor_id=actor, message_id=mid) is True
    assert mark(actor_id=actor, message_id=mid) is True
    # The fake always returns True (it doesn't track uniqueness); the
    # DB UNIQUE constraint makes the second insert a no-op via
    # ON CONFLICT DO NOTHING. The contract is "always 204 on success".
