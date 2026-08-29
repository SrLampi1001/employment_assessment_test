-- Phase 1 — 0040_functions_procedures.sql
-- Transactional functions (rw_register_user, rw_create_channel, rw_send_message)
-- and the two REQUIRED procedures (rw_edit_message, rw_delete_message).
--
-- Per ARCHITECTURE.md §3 + §5.1, the write path goes through these
-- objects so the application role never composes INSERT/UPDATE/DELETE
-- directly. The functions are SECURITY DEFINER so they can write to
-- rows that rw_app could not write to directly; the function body
-- defends in depth by checking that p_actor matches the GUC actor and
-- that the actor is a current member of the channel.

-- ─────────────────────────────────────────────────────────────────────
-- rw_register_user: creates a user + credential atomically.
-- Used by Phase 2 (auth). Validates locale and username at the DB layer.
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rw_register_user(
    p_username       varchar,
    p_display_name   varchar,
    p_locale         char(2),
    p_password_hash  text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_user_id uuid;
BEGIN
    INSERT INTO rw_user (rw_username, rw_display_name, rw_locale)
    VALUES (p_username, p_display_name, p_locale)
    RETURNING rw_id INTO v_user_id;

    INSERT INTO rw_auth_credential (rw_user_id, rw_password_hash)
    VALUES (v_user_id, p_password_hash);

    RETURN v_user_id;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────
-- rw_create_channel: creates a channel + the creator's owner membership
-- in one statement. The channel kind is required (1 = direct, 2 = group).
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rw_create_channel(
    p_name        varchar,
    p_kind        smallint,
    p_creator_id  uuid
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_channel_id uuid;
BEGIN
    INSERT INTO rw_channel (rw_name, rw_kind, rw_created_by)
    VALUES (p_name, p_kind, p_creator_id)
    RETURNING rw_id INTO v_channel_id;

    INSERT INTO rw_channel_member (rw_channel_id, rw_user_id, rw_role)
    VALUES (v_channel_id, p_creator_id, 2);  -- 2 = owner

    RETURN v_channel_id;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────
-- rw_send_message: idempotent on (rw_author_id, rw_client_ref) WHERE
-- rw_client_ref IS NOT NULL. On conflict, returns the existing row.
-- Validates that the caller (GUC actor) matches p_author_id and is a
-- current member of p_channel_id (defense in depth — RLS would also stop
-- it, but the function is called as SECURITY DEFINER and bypasses RLS).
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rw_send_message(
    p_channel_id  uuid,
    p_author_id   uuid,
    p_body        text,
    p_client_ref  varchar DEFAULT NULL
) RETURNS rw_message
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_msg rw_message;
BEGIN
    -- Defense in depth: the function is SECURITY DEFINER, so RLS does
    -- not block the insert. We check explicitly.
    IF p_author_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_send_message: author mismatch with actor GUC';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM rw_channel_member
        WHERE rw_channel_id = p_channel_id
          AND rw_user_id    = p_author_id
          AND rw_left_at   IS NULL
    ) THEN
        RAISE EXCEPTION 'rw_send_message: actor is not a current member of the channel';
    END IF;

    INSERT INTO rw_message (rw_channel_id, rw_author_id, rw_client_ref, rw_body)
    VALUES (p_channel_id, p_author_id, p_client_ref, p_body)
    ON CONFLICT (rw_author_id, rw_client_ref)
        WHERE rw_client_ref IS NOT NULL
        DO NOTHING
    RETURNING * INTO v_msg;

    IF v_msg.rw_id IS NULL THEN
        -- Idempotent retry: return the original row.
        SELECT * INTO v_msg
        FROM rw_message
        WHERE rw_author_id = p_author_id
          AND rw_client_ref = p_client_ref;
    END IF;

    RETURN v_msg;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────
-- rw_edit_message — REQUIRED procedure #1 (ARCHITECTURE.md §3).
-- Appends a rw_message_edit row and updates rw_message in place. Never
-- physically deletes the previous body.
--
-- Per issue #23, the procedure refuses non-author edits (0 rows updated).
-- The application layer maps that to 404 so a non-author cannot
-- distinguish "message does not exist" from "you are not the author" —
-- no existence leak.
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE PROCEDURE rw_edit_message(
    p_message_id  uuid,
    p_editor_id   uuid,
    p_new_body    text
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_author_id uuid;
BEGIN
    IF p_editor_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_edit_message: editor mismatch with actor GUC';
    END IF;

    -- ── Author gate (issue #23) ─────────────────────────────────────
    -- The function runs as the function owner (postgres in dev,
    -- rw_migrator in prod), which BYPASSES RLS. The rw_message_update
    -- RLS policy that requires rw_author_id = GUC does NOT fire
    -- inside this procedure body. We re-enforce it explicitly so
    -- a non-author can never overwrite someone else's message even
    -- by calling the function directly.
    SELECT rw_author_id INTO v_author_id
    FROM rw_message
    WHERE rw_id = p_message_id AND rw_deleted_at IS NULL;

    IF v_author_id IS NULL THEN
        -- Message does not exist OR is already deleted; the application
        -- maps this to 404 (no existence leak).
        RETURN;
    END IF;

    IF v_author_id <> p_editor_id THEN
        -- The actor is not the author; refuse silently so the
        -- application surfaces 404, not 403.
        RETURN;
    END IF;

    INSERT INTO rw_message_edit (rw_message_id, rw_body, rw_editor_id)
    VALUES (p_message_id, p_new_body, p_editor_id);

    UPDATE rw_message
       SET rw_body      = p_new_body,
           rw_is_edited = true,
           rw_edited_at = now()
     WHERE rw_id        = p_message_id
       AND rw_deleted_at IS NULL;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────
-- rw_delete_message — REQUIRED procedure #2 (ARCHITECTURE.md §3).
-- Logical delete only. Sets rw_deleted_at + rw_deleted_reason (both,
-- per the CHECK constraint). The row stays in rw_message so the audit
-- trail is preserved (AGENTS.md / Prohibited Actions).
--
-- Per issue #23, the procedure refuses non-author deletes (0 rows
-- updated). The application maps that to 404.
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE PROCEDURE rw_delete_message(
    p_message_id  uuid,
    p_actor_id    uuid,
    p_reason      text
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    IF p_actor_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_delete_message: actor mismatch with actor GUC';
    END IF;

    UPDATE rw_message
       SET rw_deleted_at     = now(),
           rw_deleted_reason = p_reason
     WHERE rw_id        = p_message_id
       AND rw_author_id = p_actor_id
       AND rw_deleted_at IS NULL;
END;
$$;
