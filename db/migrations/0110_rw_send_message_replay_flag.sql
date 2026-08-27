-- Phase 4 — 0110_rw_send_message_replay_flag.sql
-- Expose whether rw_send_message returned an existing row (idempotent
-- replay) or a freshly inserted one. The application layer uses this
-- to surface a distinct HTTP status (200 vs 201) so the frontend's
-- *pending → sent → failed* state machine can detect the replay
-- without needing a separate body field.
--
-- Migration is backward-compatible: the OUT parameter is optional in
-- callers using named-parameter style, and we update the function
-- signature in-place so existing callers (currently none in the
-- codebase, since the only caller is the Phase 4 adapter) pick up the
-- new shape.

DROP FUNCTION IF EXISTS rw_send_message(uuid, uuid, text, varchar);

CREATE OR REPLACE FUNCTION rw_send_message(
    p_channel_id  uuid,
    p_author_id   uuid,
    p_body        text,
    p_client_ref  varchar DEFAULT NULL,
    OUT out_was_replay       boolean,
    OUT out_rw_id            uuid,
    OUT out_rw_channel_id    uuid,
    OUT out_rw_author_id     uuid,
    OUT out_rw_client_ref    varchar,
    OUT out_rw_body          text,
    OUT out_rw_is_edited     boolean,
    OUT out_rw_created_at    timestamptz,
    OUT out_rw_edited_at     timestamptz,
    OUT out_rw_deleted_at    timestamptz,
    OUT out_rw_deleted_reason text
) RETURNS record
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
        SELECT 1 FROM rw_channel_member m
        WHERE m.rw_channel_id = p_channel_id
          AND m.rw_user_id    = p_author_id
          AND m.rw_left_at   IS NULL
    ) THEN
        RAISE EXCEPTION 'rw_send_message: actor is not a current member of the channel';
    END IF;

    out_was_replay := false;

    INSERT INTO rw_message (rw_channel_id, rw_author_id, rw_client_ref, rw_body)
    VALUES (p_channel_id, p_author_id, p_client_ref, p_body)
    ON CONFLICT (rw_author_id, rw_client_ref)
        WHERE rw_client_ref IS NOT NULL
        DO NOTHING
    RETURNING * INTO v_msg;

    IF v_msg.rw_id IS NULL THEN
        -- Idempotent retry: return the original row.
        out_was_replay := true;
        SELECT * INTO v_msg
        FROM rw_message m
        WHERE m.rw_author_id = p_author_id
          AND m.rw_client_ref = p_client_ref;
    END IF;

    out_rw_id            := v_msg.rw_id;
    out_rw_channel_id    := v_msg.rw_channel_id;
    out_rw_author_id     := v_msg.rw_author_id;
    out_rw_client_ref    := v_msg.rw_client_ref;
    out_rw_body          := v_msg.rw_body;
    out_rw_is_edited     := v_msg.rw_is_edited;
    out_rw_created_at    := v_msg.rw_created_at;
    out_rw_edited_at     := v_msg.rw_edited_at;
    out_rw_deleted_at    := v_msg.rw_deleted_at;
    out_rw_deleted_reason := v_msg.rw_deleted_reason;
END;
$$;

GRANT EXECUTE ON FUNCTION rw_send_message(uuid, uuid, text, varchar) TO rw_app;
