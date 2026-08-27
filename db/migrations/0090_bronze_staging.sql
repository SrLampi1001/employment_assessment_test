-- Phase 1 — 0090_bronze_staging.sql
-- The Bronze layer of the medallion (ARCHITECTURE.md §9).
--
-- `stg_seed_message` holds the seed corpus exactly as received, in
-- `jsonb`. The Silver load (`db/seed/load.py`) parses the jsonb and
-- normalizes it into the rw_* tables. Keeping the original payload lets
-- the loader be re-run from the "before" without re-fetching the corpus.
--
-- This table is INTENTIONALLY outside RLS: it's a dev-only artifact
-- (the loader truncates it on every load). It is not granted to rw_app.

CREATE TABLE IF NOT EXISTS stg_seed_message (
    rw_id          bigserial    PRIMARY KEY,
    rw_payload     jsonb        NOT NULL,
    rw_loaded_at   timestamptz  NOT NULL DEFAULT now()
);
