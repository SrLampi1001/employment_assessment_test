-- Phase 7 — 0150_trg_message_embedding_guard.sql
-- Closes issue #24: rename the embedding trigger + function to make
-- their GUARDRAIL role explicit. The trigger does NOT compute embeddings
-- (no HTTP from PostgreSQL). Embeddings are populated by:
--   * the application layer (MistralAdapter) on the rw_send_message(...) path
--   * the seed loader (backend/scripts/seed.py) post-load embed pass
-- This trigger simply RAISE WARNINGs if a row landed without an embedding,
-- so neither path can silently skip the embed step.

-- Drop the old trigger + the old function name so we don't leave a dead
-- alias behind (renamed, not duplicated).
DROP TRIGGER IF EXISTS trg_message_embedding ON rw_message;
DROP TRIGGER IF EXISTS trg_message_embedding_guard ON rw_message;
DROP FUNCTION IF EXISTS rw_compute_message_embedding();

CREATE OR REPLACE FUNCTION rw_guard_message_embedding() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.rw_body IS NOT NULL AND NEW.rw_embedding IS NULL THEN
        RAISE WARNING 'rw_message % inserted/updated without embedding; copilot search will skip it',
            NEW.rw_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_message_embedding_guard
    AFTER INSERT OR UPDATE OF rw_body ON rw_message
    FOR EACH ROW EXECUTE FUNCTION rw_guard_message_embedding();
