-- Phase 1 — 0030_indexes.sql
-- Indexes per ARCHITECTURE.md §2.4 and the postgresql-rls-pgvector skill (Step 3).
-- Includes the two REQUIRED partial unique indexes that gate the
-- idempotency invariant on messages and the "one active membership" rule.

-- Required partial unique index #1: a user cannot be an active member of
-- the same channel twice. Direct-message rooms (kind = 1) are also covered
-- because their membership still flows through this table.
CREATE UNIQUE INDEX IF NOT EXISTS uq_rw_channel_member_active
    ON rw_channel_member (rw_channel_id, rw_user_id)
    WHERE rw_left_at IS NULL;

-- Required partial unique index #2: idempotent message send on
-- (rw_author_id, rw_client_ref). NULL rw_client_ref values do not collide,
-- so non-idempotent sends remain allowed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_rw_message_client_ref
    ON rw_message (rw_author_id, rw_client_ref)
    WHERE rw_client_ref IS NOT NULL;

-- Keyset pagination backing index (per-channel, newest first).
-- Backs the keyset history query in ARCHITECTURE.md §6.
CREATE INDEX IF NOT EXISTS ix_rw_message_channel_created
    ON rw_message (rw_channel_id, rw_created_at DESC, rw_id DESC);

-- Unread count backing index (Phase 5 will query this).
CREATE INDEX IF NOT EXISTS ix_rw_message_read_user_message
    ON rw_message_read (rw_user_id, rw_message_id);

-- Vector ANN search (HNSW, cosine distance).
-- The `vector_cosine_ops` operator class matches the `<=>` operator used
-- by the copilot retrieval query in ARCHITECTURE.md §4.1.
CREATE INDEX IF NOT EXISTS ix_rw_message_embedding_hnsw
    ON rw_message USING hnsw (rw_embedding vector_cosine_ops);

-- Full-text search backing indexes (one per language; Phase 5 picks by user locale).
CREATE INDEX IF NOT EXISTS ix_rw_message_body_es
    ON rw_message USING gin (to_tsvector('spanish', rw_body));

CREATE INDEX IF NOT EXISTS ix_rw_message_body_en
    ON rw_message USING gin (to_tsvector('english', rw_body));

-- Channel listing backing index.
CREATE INDEX IF NOT EXISTS ix_rw_channel_member_user_active
    ON rw_channel_member (rw_user_id)
    WHERE rw_left_at IS NULL;
