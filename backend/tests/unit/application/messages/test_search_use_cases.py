"""Unit tests for the Phase 5 search + read-receipts use cases.

In-memory fakes only — no I/O. Mirrors the existing unit-test
patterns in `test_messages_use_cases.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain import SearchHit
from app.messages import (
    MarkChannelRead,
    MessageError,
    SearchMessages,
    UnreadCountForChannel,
)


# ─── Fakes ──────────────────────────────────────────────────────────────


@dataclass
class _FakeSearchRepo:
    hits: list[SearchHit] = field(default_factory=list)

    def __call__(self, conn=None) -> "_FakeSearchRepo":
        return self

    def search_in_channel(
        self, *, channel_id: UUID, query: str, limit: int
    ) -> list[SearchHit]:
        # Naive substring match — the fake just filters by `query` being
        # a substring of the body. The real DB uses ts_headline +
        # to_tsvector + plainto_tsquery. The fake doesn't care about
        # rank — the use case only checks the input validation + the
        # projection.
        return [
            h for h in self.hits
            if h.rw_channel_id == channel_id and query in h.rw_body
        ][:limit]


@dataclass
class _FakeSession:
    def __call__(self):
        from tests.unit.application.auth.test_use_cases import _NullConnection
        return _NullConnection()


@dataclass
class _FakeMessageRepoWithRead:
    """Extends _FakeMessageRepo's role with mark_channel_read +
    unread_count_for_channel so the Phase 5 use cases can be tested
    in isolation (no I/O)."""

    unread: dict[tuple[UUID, UUID], int] = field(default_factory=dict)
    inserted: list[tuple[UUID, UUID]] = field(default_factory=list)

    def __call__(self, conn=None) -> "_FakeMessageRepoWithRead":
        return self

    def mark_channel_read(
        self, *, channel_id: UUID, user_id: UUID
    ) -> int:
        # Pretend the channel has 3 visible messages, all unread.
        # Idempotent: a re-call inserts 0.
        key = (channel_id, user_id)
        if key in self.inserted:
            return 0
        self.inserted.append(key)
        return self.unread.get(key, 3)

    def unread_count_for_channel(
        self, *, channel_id: UUID, user_id: UUID
    ) -> int:
        return self.unread.get((channel_id, user_id), 0)


def _setup_search() -> tuple[dict[str, Any], UUID]:
    repo = _FakeSearchRepo()
    sf = _FakeSession()
    actor = uuid4()
    return {"repo": repo, "sf": sf}, actor


# ─── SearchMessages ─────────────────────────────────────────────────────


def test_search_returns_highlighted_hits_with_is_mine_flag() -> None:
    ctx, actor = _setup_search()
    repo: _FakeSearchRepo = ctx["repo"]
    repo.hits.append(
        SearchHit(
            rw_id=uuid4(),
            rw_channel_id=uuid4(),
            rw_author_id=actor,
            rw_body="hola mundo",
            rw_created_at=datetime.now(timezone.utc),
            rw_highlight="<mark>hola</mark> mundo",
        )
    )
    repo.hits.append(
        SearchHit(
            rw_id=uuid4(),
            rw_channel_id=uuid4(),
            rw_author_id=uuid4(),  # not the actor
            rw_body="adios",
            rw_created_at=datetime.now(timezone.utc),
            rw_highlight="adios",
        )
    )

    search = SearchMessages(
        session_factory=ctx["sf"],
        search_repo_factory=ctx["repo"],
    )
    hits = search(
        actor_id=actor,
        channel_id=repo.hits[0].rw_channel_id,
        query="hola",
    )
    assert len(hits) == 1
    assert hits[0].is_mine is True
    assert hits[0].rw_highlight.startswith("<mark>")


def test_search_rejects_empty_query() -> None:
    ctx, actor = _setup_search()
    search = SearchMessages(
        session_factory=ctx["sf"], search_repo_factory=ctx["repo"]
    )
    with pytest.raises(MessageError) as exc:
        search(actor_id=actor, channel_id=uuid4(), query="")
    assert exc.value.code == "invalid-query"


def test_search_rejects_oversized_query() -> None:
    ctx, actor = _setup_search()
    search = SearchMessages(
        session_factory=ctx["sf"], search_repo_factory=ctx["repo"]
    )
    with pytest.raises(MessageError) as exc:
        search(actor_id=actor, channel_id=uuid4(), query="x" * 201)
    assert exc.value.code == "invalid-query"


def test_search_rejects_invalid_limit() -> None:
    ctx, actor = _setup_search()
    search = SearchMessages(
        session_factory=ctx["sf"], search_repo_factory=ctx["repo"]
    )
    with pytest.raises(MessageError) as exc:
        search(actor_id=actor, channel_id=uuid4(), query="ok", limit=0)
    assert exc.value.code == "invalid-limit"
    with pytest.raises(MessageError):
        search(actor_id=actor, channel_id=uuid4(), query="ok", limit=51)


# ─── MarkChannelRead ────────────────────────────────────────────────────


def test_mark_channel_read_returns_inserted_count() -> None:
    repo = _FakeMessageRepoWithRead()
    repo.unread[(uuid4(), uuid4())] = 3
    sf = _FakeSession()
    mark = MarkChannelRead(session_factory=sf, message_repo_factory=repo)

    channel_id = uuid4()
    user_id = uuid4()
    inserted = mark(actor_id=user_id, channel_id=channel_id)
    assert inserted == 3
    assert (channel_id, user_id) in repo.inserted


def test_mark_channel_read_is_idempotent() -> None:
    repo = _FakeMessageRepoWithRead()
    sf = _FakeSession()
    mark = MarkChannelRead(session_factory=sf, message_repo_factory=repo)
    channel_id = uuid4()
    user_id = uuid4()
    repo.unread[(channel_id, user_id)] = 5
    assert mark(actor_id=user_id, channel_id=channel_id) == 5
    # Second call: already in `inserted`, so 0
    assert mark(actor_id=user_id, channel_id=channel_id) == 0


# ─── UnreadCountForChannel ──────────────────────────────────────────────


def test_unread_count_returns_zero_for_unknown_channel() -> None:
    repo = _FakeMessageRepoWithRead()
    sf = _FakeSession()
    uc = UnreadCountForChannel(session_factory=sf, message_repo_factory=repo)
    assert uc(actor_id=uuid4(), channel_id=uuid4()) == 0


def test_unread_count_returns_seeded_value() -> None:
    repo = _FakeMessageRepoWithRead()
    sf = _FakeSession()
    uc = UnreadCountForChannel(session_factory=sf, message_repo_factory=repo)
    channel_id = uuid4()
    user_id = uuid4()
    repo.unread[(channel_id, user_id)] = 7
    assert uc(actor_id=user_id, channel_id=channel_id) == 7