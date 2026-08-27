-- Phase 1 — 0070_views.sql
-- The "Gold" consumption view from ARCHITECTURE.md §9.
--
-- rw_visible_message is a thin filter on rw_message that hides logically
-- deleted rows. RLS still applies through the underlying table — non-members
-- see zero rows, deleted messages never reach the application.
--
-- IMPORTANT (PostgreSQL 15+ behaviour): the default for views is
-- security_invoker = false, which means queries against the view run as
-- the view OWNER (the migrator / postgres superuser), which BYPASSES RLS.
-- That would silently re-introduce the leak the policies are here to close.
-- The WITH (security_invoker = true) clause makes the view run with the
-- INVOKING user's permissions, so RLS on rw_message applies.

DROP VIEW IF EXISTS rw_visible_message;
CREATE VIEW rw_visible_message
    WITH (security_invoker = true)
AS
    SELECT *
    FROM rw_message
    WHERE rw_deleted_at IS NULL;
