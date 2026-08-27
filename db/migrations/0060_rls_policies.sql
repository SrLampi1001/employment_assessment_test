-- Phase 1 — 0060_rls_policies.sql
-- The executable security spec (ARCHITECTURE.md §3).
-- The application role rw_app has NO BYPASSRLS — these policies are
-- the only thing standing between the actor's JWT and the row.
--
-- Pattern: every policy reads the actor from `app.current_user_id`.
-- Note the second argument `true` to current_setting — returns NULL
-- instead of erroring when the GUC is unset; the ::uuid cast then
-- fails closed (returns zero rows) instead of failing open.

-- ─────────────────────────────────────────────────────────────────────
-- Enable RLS on every rw_* table that carries user-visible data.
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE rw_channel       ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_channel_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_message       ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_message_edit  ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_message_read  ENABLE ROW LEVEL SECURITY;

-- ─────────────────────────────────────────────────────────────────────
-- rw_message: the centerpiece policy.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_message_visibility ON rw_message;
CREATE POLICY rw_message_visibility ON rw_message
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM rw_channel_member m
            WHERE m.rw_channel_id = rw_message.rw_channel_id
              AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
              AND m.rw_left_at   IS NULL
        )
    );

DROP POLICY IF EXISTS rw_message_insert ON rw_message;
CREATE POLICY rw_message_insert ON rw_message
    FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM rw_channel_member m
            WHERE m.rw_channel_id = rw_message.rw_channel_id
              AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
              AND m.rw_left_at   IS NULL
        )
        AND rw_author_id = current_setting('app.current_user_id', true)::uuid
    );

DROP POLICY IF EXISTS rw_message_update ON rw_message;
CREATE POLICY rw_message_update ON rw_message
    FOR UPDATE
    USING (
        rw_author_id = current_setting('app.current_user_id', true)::uuid
        AND EXISTS (
            SELECT 1 FROM rw_channel_member m
            WHERE m.rw_channel_id = rw_message.rw_channel_id
              AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
              AND m.rw_left_at   IS NULL
        )
    )
    WITH CHECK (rw_deleted_at IS NULL);   -- logical delete goes through rw_delete_message()

-- ─────────────────────────────────────────────────────────────────────
-- rw_message_edit: same membership check, joined through the message.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_message_edit_visibility ON rw_message_edit;
CREATE POLICY rw_message_edit_visibility ON rw_message_edit
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM rw_message msg
            JOIN rw_channel_member m
              ON m.rw_channel_id = msg.rw_channel_id
             AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
             AND m.rw_left_at   IS NULL
            WHERE msg.rw_id = rw_message_edit.rw_message_id
        )
    );

DROP POLICY IF EXISTS rw_message_edit_insert ON rw_message_edit;
CREATE POLICY rw_message_edit_insert ON rw_message_edit
    FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM rw_message msg
            JOIN rw_channel_member m
              ON m.rw_channel_id = msg.rw_channel_id
             AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
             AND m.rw_left_at   IS NULL
            WHERE msg.rw_id = rw_message_edit.rw_message_id
        )
        AND rw_editor_id = current_setting('app.current_user_id', true)::uuid
    );

-- ─────────────────────────────────────────────────────────────────────
-- rw_message_read: an actor reads/marks receipts for messages in
-- channels they belong to. The author doesn't need to be the recipient.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_message_read_visibility ON rw_message_read;
CREATE POLICY rw_message_read_visibility ON rw_message_read
    FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM rw_message msg
            JOIN rw_channel_member m
              ON m.rw_channel_id = msg.rw_channel_id
             AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
             AND m.rw_left_at   IS NULL
            WHERE msg.rw_id = rw_message_read.rw_message_id
        )
        AND rw_user_id = current_setting('app.current_user_id', true)::uuid
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM rw_message msg
            JOIN rw_channel_member m
              ON m.rw_channel_id = msg.rw_channel_id
             AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
             AND m.rw_left_at   IS NULL
            WHERE msg.rw_id = rw_message_read.rw_message_id
        )
        AND rw_user_id = current_setting('app.current_user_id', true)::uuid
    );

-- ─────────────────────────────────────────────────────────────────────
-- rw_channel: actor sees channels they're a current member of.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_channel_visibility ON rw_channel;
CREATE POLICY rw_channel_visibility ON rw_channel
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM rw_channel_member m
            WHERE m.rw_channel_id = rw_channel.rw_id
              AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
              AND m.rw_left_at   IS NULL
        )
    );

-- ─────────────────────────────────────────────────────────────────────
-- rw_channel_member: actor sees their own memberships (past + present).
-- They can also INSERT a new membership row for themselves (the add-member
-- flow in Phase 3 enforces "channel owner only" at the application layer).
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS rw_channel_member_self ON rw_channel_member;
CREATE POLICY rw_channel_member_self ON rw_channel_member
    FOR ALL
    USING (rw_user_id = current_setting('app.current_user_id', true)::uuid)
    WITH CHECK (rw_user_id = current_setting('app.current_user_id', true)::uuid);
