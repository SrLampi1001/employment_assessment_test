-- Phase 1 — 0050_triggers.sql
-- Keeps rw_embedding in lockstep with rw_body. Embeddings are computed
-- in the application layer (infrastructure/ai/MistralAdapter) and
-- passed in the INSERT/UPDATE. This trigger is a GUARDRAIL: it warns
-- if a row landed without one, so the seed script can't silently skip
-- the embed step.

CREATE OR REPLACE FUNCTION rw_compute_message_embedding() RETURNS trigger
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

DROP TRIGGER IF EXISTS trg_message_embedding ON rw_message;
CREATE TRIGGER trg_message_embedding
    AFTER INSERT OR UPDATE OF rw_body ON rw_message
    FOR EACH ROW EXECUTE FUNCTION rw_compute_message_embedding();
