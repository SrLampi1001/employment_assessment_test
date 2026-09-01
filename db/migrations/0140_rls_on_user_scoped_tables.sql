-- Phase 7 — 0140_rls_on_user_scoped_tables.sql
-- RLS hardening per ARCHITECTURE.md §3 + issue #22: enable Row-Level
-- Security on `rw_refresh_token` and `rw_copilot_usage`, the two
-- tables that carry per-user state but were skipped in Phase 1 because
-- they were originally wired as direct SQL access from the runtime.
--
-- Why a SECURITY DEFINER layer instead of just `ENABLE ROW LEVEL SECURITY`:
--
-- `rw_refresh_token` is read during the Login flow BEFORE the actor
-- has a JWT — the RwSession is opened with `actor_id=None` and there
-- is no GUC value to check against. A pure RLS policy keyed on
-- `rw_user_id = GUC` would BLOCK the entire auth path.
--
-- `rw_copilot_usage` is INSERTed during a normal authenticated
-- request, but the audit record needs to be guaranteed; doing the
-- INSERT through a SECURITY DEFINER function means the application's
-- `rw_app` role (no BYPASSRLS) can never accidentally bypass the
-- write path or compose ad-hoc INSERTs.
--
-- The pattern matches Phase 3 (`rw_add_channel_member`): RLS is the
-- row-level filter, the function is the API surface, the application
-- role never composes INSERT/UPDATE/DELETE directly on these tables.

-- ─────────────────────────────────────────────────────────────────────
-- rw_refresh_token — five SECURITY DEFINER functions that cover every
-- read/write the runtime currently does.
-- ─────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION rw_insert_refresh_token(
    p_user_id     uuid,
    p_token_hash  text,
    p_family_id   uuid,
    p_expires_at  timestamptz
) RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    INSERT INTO rw_refresh_token
        (rw_user_id, rw_token_hash, rw_family_id, rw_expires_at)
    VALUES (p_user_id, p_token_hash, p_family_id, p_expires_at);
$$;

CREATE OR REPLACE FUNCTION rw_find_refresh_token(
    p_token_hash  text
) RETURNS TABLE (
    rw_id          uuid,
    rw_user_id     uuid,
    rw_token_hash  text,
    rw_family_id   uuid,
    rw_expires_at  timestamptz,
    rw_revoked_at  timestamptz
)
LANGUAGE sql
SECURITY DEFINER
AS $$
    SELECT rw_id, rw_user_id, rw_token_hash, rw_family_id,
           rw_expires_at, rw_revoked_at
    FROM rw_refresh_token
    WHERE rw_token_hash = p_token_hash;
$$;

CREATE OR REPLACE FUNCTION rw_revoke_refresh_token(
    p_token_id  uuid
) RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    UPDATE rw_refresh_token SET rw_revoked_at = now()
    WHERE rw_id = p_token_id AND rw_revoked_at IS NULL;
$$;

CREATE OR REPLACE FUNCTION rw_revoke_refresh_token_family(
    p_family_id  uuid
) RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    -- Single statement, family-wide, idempotent (only revokes
    -- non-revoked rows). See the contract test in
    -- tests/unit/application/auth/test_refresh.py::test_reuse_detection_revokes_entire_family.
    UPDATE rw_refresh_token SET rw_revoked_at = now()
    WHERE rw_family_id = p_family_id AND rw_revoked_at IS NULL;
$$;

-- ─────────────────────────────────────────────────────────────────────
-- rw_copilot_usage — single INSERT function (the §11.4 audit hook).
-- The aggregate read (`fetch_copilot_usage_summary`) keeps using a
-- parameterized SELECT: with RLS on, the actor can only see their own
-- rows, which is exactly what the aggregate wants.
-- ─────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION rw_record_copilot_usage(
    p_user_id            uuid,
    p_model              varchar,
    p_prompt_tokens      int,
    p_completion_tokens  int
) RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    INSERT INTO rw_copilot_usage
        (rw_user_id, rw_model, rw_prompt_tokens, rw_completion_tokens)
    VALUES (p_user_id, p_model, p_prompt_tokens, p_completion_tokens);
$$;

-- ─────────────────────────────────────────────────────────────────────
-- Enable RLS on both tables.
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE rw_refresh_token  ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_copilot_usage  ENABLE ROW LEVEL SECURITY;

-- ─────────────────────────────────────────────────────────────────────
-- rw_refresh_token policy — per-user only. The function layer above
-- runs as the migrator (SECURITY DEFINER) so RLS is bypassed for
-- the explicit auth path; this policy blocks ANY stray direct
-- access that bypasses the functions.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_refresh_token_owner ON rw_refresh_token;
CREATE POLICY rw_refresh_token_owner ON rw_refresh_token
    FOR ALL
    USING (rw_user_id = current_setting('app.current_user_id', true)::uuid)
    WITH CHECK (rw_user_id = current_setting('app.current_user_id', true)::uuid);

-- ─────────────────────────────────────────────────────────────────────
-- rw_copilot_usage policy — per-user only. The §11.4 usage summary
-- (`fetch_copilot_usage_summary`) is RLS-aware: it only ever returns
-- the actor's own aggregates.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_copilot_usage_owner ON rw_copilot_usage;
CREATE POLICY rw_copilot_usage_owner ON rw_copilot_usage
    FOR ALL
    USING (rw_user_id = current_setting('app.current_user_id', true)::uuid)
    WITH CHECK (rw_user_id = current_setting('app.current_user_id', true)::uuid);

-- ─────────────────────────────────────────────────────────────────────
-- Grants — runtime role can only invoke the SECURITY DEFINER
-- functions on these two tables; direct table access is revoked so
-- the application's `rw_app` role has no way to compose a stray
-- SELECT/INSERT/UPDATE/DELETE that bypasses the function layer.
-- ─────────────────────────────────────────────────────────────────────

REVOKE ALL ON rw_refresh_token FROM rw_app;
-- rw_copilot_usage: the §11.4 usage summary endpoint reads aggregated
-- counts (`SELECT count(*), sum(...) FROM rw_copilot_usage WHERE
-- rw_user_id = %s`). RLS still scopes the actor to their own rows,
-- so we GRANT SELECT to the runtime role; the row-level policy
-- remains the only filter.
REVOKE INSERT, UPDATE, DELETE ON rw_copilot_usage FROM rw_app;
GRANT SELECT ON rw_copilot_usage TO rw_app;

GRANT EXECUTE ON FUNCTION rw_insert_refresh_token(uuid, text, uuid, timestamptz)
    TO rw_app;
GRANT EXECUTE ON FUNCTION rw_find_refresh_token(text)
    TO rw_app;
GRANT EXECUTE ON FUNCTION rw_revoke_refresh_token(uuid)
    TO rw_app;
GRANT EXECUTE ON FUNCTION rw_revoke_refresh_token_family(uuid)
    TO rw_app;
GRANT EXECUTE ON FUNCTION rw_record_copilot_usage(uuid, varchar, int, int)
    TO rw_app;