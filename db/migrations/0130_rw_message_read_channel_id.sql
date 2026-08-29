-- Phase 6 follow-up — 0130_rw_message_read_channel_id.sql
-- Adds rw_channel_id to rw_message_read so the index ARCH §2.4 mandates
-- — (rw_user_id, rw_channel_id) — can be created.
--
-- The table was originally normalized (rw_message_read → rw_message →
-- rw_channel); deriving the channel at query time needs a join, which
-- defeats the purpose of the unread-count index. We denormalise by
-- copying the channel_id at insert time, kept in sync by a BEFORE
-- INSERT trigger. This is the same pattern as Postgres' own
-- `pg_statistic.stainherit` derived columns — controlled, scoped,
-- auditable denormalisation for index coverage.
--
-- Idempotent: each step uses IF NOT EXISTS / DO blocks so re-running
-- the migration on an already-converged database is a no-op.

-- 1. Add the column nullable so the ALTER TABLE itself can run before
--    backfill. NOT NULL comes after backfill.
ALTER TABLE rw_message_read
    ADD COLUMN IF NOT EXISTS rw_channel_id uuid
        REFERENCES rw_channel(rw_id);

-- 2. Backfill any rows that already exist (e.g. from a partial Phase
--    5 load). After this the column has no NULLs that weren't there
--    because of a missing rw_message (which would be an existing
--    referential-integrity problem, not ours to fix).
UPDATE rw_message_read r
SET    rw_channel_id = m.rw_channel_id
FROM   rw_message    m
WHERE  m.rw_id = r.rw_message_id
  AND  r.rw_channel_id IS NULL;

-- 3. Lock the column as NOT NULL. Any future insert with NULL is a
--    programming error and the trigger below would already have
--    populated it, so this is a belt-and-braces invariant.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name  = 'rw_message_read'
          AND column_name = 'rw_channel_id'
          AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE rw_message_read
            ALTER COLUMN rw_channel_id SET NOT NULL;
    END IF;
END
$$;

-- 4. Trigger: BEFORE INSERT keeps rw_channel_id in lockstep with the
--    referenced rw_message. Application code keeps writing
--    (rw_message_id, rw_user_id) — the trigger fills the third column
--    from the message row, so no caller changes are required.
CREATE OR REPLACE FUNCTION rw_message_read_set_channel()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.rw_channel_id IS NULL THEN
        SELECT rw_channel_id INTO NEW.rw_channel_id
        FROM   rw_message
        WHERE  rw_id = NEW.rw_message_id;

        IF NEW.rw_channel_id IS NULL THEN
            RAISE EXCEPTION
                'rw_message_read: rw_message_id % does not exist',
                NEW.rw_message_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_message_read_channel ON rw_message_read;
CREATE TRIGGER trg_message_read_channel
    BEFORE INSERT ON rw_message_read
    FOR EACH ROW
    EXECUTE FUNCTION rw_message_read_set_channel();

-- 5. Index that ARCH §2.4 mandates. The 0030_indexes.sql comment
--    points to this migration as the place where the column lands.
--    We replace the (rw_user_id, rw_message_id) index created in 0030
--    with the column-order this section requires.
DROP INDEX IF EXISTS ix_rw_message_read_user_message;
CREATE INDEX IF NOT EXISTS ix_rw_message_read_user_channel
    ON rw_message_read (rw_user_id, rw_channel_id);