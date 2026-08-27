-- Phase 0 — 0002_roles.sql
-- Create the runtime + migration roles.
-- See AGENTS.md / Prohibited Actions: rw_app MUST be NOLOGIN,
-- MUST NOT have BYPASSRLS, MUST NOT have SUPERUSER.
--
-- Passwords below are dev-only placeholders. Production overrides via env
-- vars (see Phase 7 — .env.example).

-- rw_migrator: owns the DDL; the migration tool connects as this role.
CREATE ROLE rw_migrator WITH LOGIN PASSWORD 'dev_migrator_pwd';

-- rw_app: the runtime role the application's connection assumes. NOLOGIN
-- so the app cannot connect as it directly. The connection string uses
-- rw_app_login; SET LOCAL app.current_user_id is scoped in RwSession
-- (Phase 2+) so every query runs under rw_app's privilege posture.
CREATE ROLE rw_app NOLOGIN;

-- rw_app_login: the role the application's DATABASE_URL uses.
-- IN ROLE rw_app makes it inherit rw_app's privileges — and crucially,
-- the lack of BYPASSRLS — so RLS policies (Phase 1) apply.
CREATE ROLE rw_app_login WITH LOGIN PASSWORD 'dev_app_pwd' IN ROLE rw_app;
