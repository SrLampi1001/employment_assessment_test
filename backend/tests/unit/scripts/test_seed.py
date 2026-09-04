"""Unit tests for backend/scripts/seed.py.

These tests use the same `pgvector/pgvector:pg18` testcontainer as the
BDD scenarios — the loader runs against a real DB, not a mock. Mocking
the SQL would defeat the point: the loader's job is to bridge jsonb and
the relational schema, and the only way to prove the bridge is correct
is to run it against PostgreSQL.

The tests focus on five contracts:

1. **Counts** — the loader returns the row counts that match the seed.
2. **Bronze + Silver integrity** — Bronze row exists; Silver FKs resolve.
3. **Idempotency** — running the loader twice produces the same end state.
4. **RLS integrity** — after a load, the seeded data respects the same
   row-level security rules the BDD scenarios assert.
5. **Embed pass (issue #24)** — when an `EmbeddingProvider` is injected,
   every seeded message receives a non-null `rw_embedding`. Without an
   embedder, the pass is skipped (back-compat with the pre-fix loader).
"""

from __future__ import annotations

import psycopg
import pytest

from scripts.seed import SeedCounts, load_from_payload


SEED_PAYLOAD = {
    "users": [
        {"username": "camila",    "display_name": "Camila",    "locale": "es", "password": "dev"},
        {"username": "valentina", "display_name": "Valentina", "locale": "es", "password": "dev"},
        {"username": "andres",    "display_name": "Andres",    "locale": "en", "password": "dev"},
    ],
    "channels": [
        {
            "name": "team-1",
            "kind": "group",
            "owner": "camila",
            "members": ["camila", "valentina", "andres"],
            "messages": [
                {"author": "camila",    "created_at": "2026-08-26T09:00:00Z", "body": "hola"},
                {"author": "valentina", "created_at": "2026-08-26T09:01:00Z", "body": "hola"},
                {"author": "andres",    "created_at": "2026-08-26T09:02:00Z", "body": "hi"},
            ],
        },
        {
            "name": "Camila-private",
            "kind": "direct",
            "owner": "camila",
            "members": ["camila", "andres"],
            "messages": [
                {"author": "camila", "created_at": "2026-08-26T10:00:00Z", "body": "private 1"},
                {"author": "andres", "created_at": "2026-08-26T10:01:00Z", "body": "private 2"},
            ],
        },
    ],
}


@pytest.fixture
def loaded_db(super_conn: psycopg.Connection) -> psycopg.Connection:
    """Apply migrations + run the loader once; yield the superuser conn."""
    counts = load_from_payload(super_conn, SEED_PAYLOAD)
    super_conn.commit()
    assert counts == SeedCounts(
        users=3, channels=2, memberships=5, messages=5, embedded=0
    )
    return super_conn


# ─── Test 1: Counts ──────────────────────────────────────────────────────


def test_loader_returns_expected_counts(loaded_db: psycopg.Connection) -> None:
    """The loader returns counts that match the seed.json in-memory payload."""
    with loaded_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM rw_user")
        assert cur.fetchone()[0] == 3
        cur.execute("SELECT count(*) FROM rw_channel")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM rw_channel_member")
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT count(*) FROM rw_message")
        assert cur.fetchone()[0] == 5


# ─── Test 2: Bronze row + Silver integrity ───────────────────────────────


def test_bronze_layer_is_a_single_payload_row(
    loaded_db: psycopg.Connection,
) -> None:
    """The Bronze layer is one row with the full payload as jsonb."""
    with loaded_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM stg_seed_message")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT jsonb_array_length(rw_payload->'users') FROM stg_seed_message")
        assert cur.fetchone()[0] == 3


def test_silver_layer_resolves_foreign_keys(
    loaded_db: psycopg.Connection,
) -> None:
    """Every rw_message has a real channel + author; every membership has a real channel + user."""
    with loaded_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM rw_message msg "
            "WHERE NOT EXISTS (SELECT 1 FROM rw_channel WHERE rw_id = msg.rw_channel_id) "
            "   OR NOT EXISTS (SELECT 1 FROM rw_user    WHERE rw_id = msg.rw_author_id)"
        )
        assert cur.fetchone()[0] == 0, "orphan messages in the Silver layer"

        cur.execute(
            "SELECT count(*) FROM rw_channel_member m "
            "WHERE NOT EXISTS (SELECT 1 FROM rw_channel WHERE rw_id = m.rw_channel_id) "
            "   OR NOT EXISTS (SELECT 1 FROM rw_user    WHERE rw_id = m.rw_user_id)"
        )
        assert cur.fetchone()[0] == 0, "orphan memberships in the Silver layer"


def test_channel_kind_mapping(super_conn: psycopg.Connection) -> None:
    """The 'direct' / 'group' strings in seed.json map to smallint 1 / 2."""
    load_from_payload(super_conn, SEED_PAYLOAD)
    super_conn.commit()
    with super_conn.cursor() as cur:
        cur.execute("SELECT rw_name, rw_kind FROM rw_channel ORDER BY rw_name")
        rows = dict(cur.fetchall())
        assert rows["Camila-private"] == 1
        assert rows["team-1"] == 2


# ─── Test 3: Idempotency ─────────────────────────────────────────────────


def test_loader_is_idempotent(super_conn: psycopg.Connection) -> None:
    """Running the loader twice produces the same end state (same counts)."""
    first = load_from_payload(super_conn, SEED_PAYLOAD)
    super_conn.commit()
    second = load_from_payload(super_conn, SEED_PAYLOAD)
    super_conn.commit()
    assert first == second
    with super_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM rw_user")
        assert cur.fetchone()[0] == 3  # not 6
        cur.execute("SELECT count(*) FROM rw_message")
        assert cur.fetchone()[0] == 5  # not 10
        cur.execute("SELECT count(*) FROM stg_seed_message")
        assert cur.fetchone()[0] == 1  # Bronze is also truncated, not appended


# ─── Test 4: RLS integrity end-to-end ────────────────────────────────────


def test_loaded_data_respects_rls(
    actor_conn: psycopg.Connection,
    loaded_db: psycopg.Connection,
) -> None:
    """After the loader runs, the BDD's RLS rules still hold for the new data.

    Connects as rw_app_login (no BYPASSRLS), sets the actor GUC to
    Valentina, and confirms the 2 Camila-private messages are invisible.
    """
    with loaded_db.cursor() as cur:
        cur.execute(
            "SELECT rw_id FROM rw_user WHERE rw_username = 'valentina'"
        )
        (valentina_id,) = cur.fetchone()

    actor_conn.execute(
        "SELECT set_config('app.current_user_id', %s, false)",
        (str(valentina_id),),
    )
    actor_conn.commit()
    with actor_conn.cursor() as cur:
        cur.execute(
            "SELECT msg.rw_id FROM rw_visible_message msg "
            "JOIN rw_channel ch ON ch.rw_id = msg.rw_channel_id "
            "WHERE ch.rw_name = 'Camila-private'"
        )
        rows = cur.fetchall()
        assert rows == [], (
            f"Valentina saw {len(rows)} messages in Camila-private — "
            "the loader populated the row but RLS is gating it correctly. "
            "This is the contract; if you see rows here, RLS is broken."
        )


def test_loaded_data_direct_channels_have_two_members(
    loaded_db: psycopg.Connection,
) -> None:
    """Direct channels (rw_kind = 1) must have exactly two active members."""
    with loaded_db.cursor() as cur:
        cur.execute(
            "SELECT ch.rw_name, count(m.rw_id) FROM rw_channel ch "
            "LEFT JOIN rw_channel_member m "
            "  ON m.rw_channel_id = ch.rw_id AND m.rw_left_at IS NULL "
            "WHERE ch.rw_kind = 1 "
            "GROUP BY ch.rw_name"
        )
        rows = dict(cur.fetchall())
        for name, count in rows.items():
            assert count == 2, (
                f"Direct channel {name!r} has {count} active members; "
                "the schema invariant (ARCHITECTURE §2.5) requires exactly 2"
            )


# ─── Test 5: Schema validation ──────────────────────────────────────────


def test_seed_loader_rejects_malformed_payload(
    super_conn: psycopg.Connection,
) -> None:
    """The loader raises ValueError when the top-level keys are missing."""
    with pytest.raises(ValueError, match="must contain top-level"):
        load_from_payload(super_conn, {"users": []})  # 'channels' missing
    super_conn.rollback()


# ─── Test 6: Post-load embed pass (issue #24) ─────────────────────────────


def test_load_without_embedder_skips_embed_pass(
    super_conn: psycopg.Connection,
) -> None:
    """Back-compat: no embedder → messages land with rw_embedding IS NULL.

    The pre-fix loader did exactly this. The fix (issue #24) adds the
    embed pass *behind* the optional `embedder` parameter; the existing
    callers and test fixtures must keep working unchanged.
    """
    counts = load_from_payload(super_conn, SEED_PAYLOAD)
    super_conn.commit()
    assert counts.embedded == 0
    with super_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM rw_message WHERE rw_embedding IS NULL")
        null_count = cur.fetchone()[0]
        assert null_count == 5, (
            f"expected 5 messages with NULL embeddings (no embedder passed), "
            f"got {null_count}"
        )


def test_load_with_embedder_populates_every_message(
    super_conn: psycopg.Connection,
) -> None:
    """When an `EmbeddingProvider` is injected, every message gets embedded.

    Uses the production `FakeEmbeddingProvider` from the BDD suite so the
    vectors are real `vector(1024)` (matches the schema). Closes issue
    #24: before this fix, seeded messages were invisible to HNSW because
    the trigger only RAISE WARNINGed about the missing embedding.
    """
    from tests.fake_chat_provider import FakeEmbeddingProvider

    embedder = FakeEmbeddingProvider()
    counts = load_from_payload(super_conn, SEED_PAYLOAD, embedder=embedder)
    super_conn.commit()
    assert counts == SeedCounts(
        users=3, channels=2, memberships=5, messages=5, embedded=5
    )
    with super_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM rw_message WHERE rw_embedding IS NULL")
        null_count = cur.fetchone()[0]
        assert null_count == 0, (
            "issue #24 regression: embedder passed but some rw_embedding "
            f"still NULL ({null_count} rows)"
        )
        # Spot-check the shape: pgvector returns a string like '[1,0,...]'.
        cur.execute("SELECT rw_embedding::text FROM rw_message LIMIT 1")
        (vec_text,) = cur.fetchone()
        assert vec_text.startswith("["), vec_text


def test_load_with_embedder_calls_batched(
    super_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embed pass batches the bodies into ≤512-text calls.

    Asserts the contract from `ai-provider-integration` (Mistral free-tier
    friendliness) without coupling the test to `MistralAdapter` itself —
    uses a counting fake so the assertion is about the call shape, not the
    implementation.
    """
    from typing import Protocol

    class _CountingEmbedder:
        def __init__(self) -> None:
            self.batches: list[list[str]] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.batches.append(list(texts))
            # 1024-dim vector; one per text, deterministic.
            return [[1.0] + [0.0] * 1023 for _ in texts]

    counter = _CountingEmbedder()
    counts = load_from_payload(super_conn, SEED_PAYLOAD, embedder=counter)
    super_conn.commit()
    assert counts.embedded == 5
    assert len(counter.batches) == 1, (
        "5 seeded bodies should fit in a single ≤512 batch"
    )
    assert len(counter.batches[0]) == 5


def test_embed_pass_is_idempotent(
    super_conn: psycopg.Connection,
) -> None:
    """Re-running the loader with an embedder leaves every row embedded.

    The pass only fills rows where `rw_embedding IS NULL` (after the
    TRUNCATE). On a re-run, all rows are NULL again (TRUNCATE clears the
    table) and the pass fills them again — confirms the SELECT filters
    correctly.
    """
    from tests.fake_chat_provider import FakeEmbeddingProvider

    embedder = FakeEmbeddingProvider()
    first = load_from_payload(super_conn, SEED_PAYLOAD, embedder=embedder)
    super_conn.commit()
    second = load_from_payload(super_conn, SEED_PAYLOAD, embedder=embedder)
    super_conn.commit()
    assert first.embedded == 5
    assert second.embedded == 5
    with super_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM rw_message WHERE rw_embedding IS NOT NULL")
        assert cur.fetchone()[0] == 5
