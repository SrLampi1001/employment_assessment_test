-- Phase 1 — 0080_grants.sql
-- Table-level grants to the runtime role rw_app. RLS is the row-level
-- filter, but the standard GRANTs are still needed so the role can
-- issue SELECT/INSERT/UPDATE/DELETE at all.
--
-- Idempotent (re-running GRANTs to the same role is a no-op).

GRANT SELECT, INSERT, UPDATE, DELETE ON rw_user            TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_auth_credential TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_channel         TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_channel_member  TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_message         TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_message_edit    TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_message_read    TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_refresh_token   TO rw_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rw_copilot_usage   TO rw_app;

GRANT SELECT ON rw_visible_message TO rw_app, PUBLIC;

-- Functions and procedures: rw_app must be able to EXECUTE them, but
-- because they are SECURITY DEFINER, the actual writes happen under the
-- migrator role's privileges (no BYPASSRLS is granted anywhere).
GRANT EXECUTE ON FUNCTION  rw_register_user(varchar, varchar, char(2), text) TO rw_app;
GRANT EXECUTE ON FUNCTION  rw_create_channel(varchar, smallint, uuid)        TO rw_app;
GRANT EXECUTE ON FUNCTION  rw_send_message(uuid, uuid, text, varchar)        TO rw_app;
GRANT EXECUTE ON PROCEDURE rw_edit_message(uuid, uuid, text)                 TO rw_app;
GRANT EXECUTE ON PROCEDURE rw_delete_message(uuid, uuid, text)               TO rw_app;
