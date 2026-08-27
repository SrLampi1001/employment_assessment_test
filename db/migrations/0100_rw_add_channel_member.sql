-- Phase 3 — 0100_rw_add_channel_member.sql
-- Add a SECURITY DEFINER function so the channel owner can add members.
--
-- Why a function and not a plain INSERT: the rw_channel_member RLS policy
-- (0060_rls_policies.sql) restricts INSERT to rows where rw_user_id matches
-- the GUC actor — so the owner cannot directly INSERT a row for someone
-- else. The SECURITY DEFINER function runs as the migrator (which can
-- write) and enforces the "inviter is owner" rule in its body.

CREATE OR REPLACE FUNCTION rw_add_channel_member(
    p_channel_id    uuid,
    p_inviter_id    uuid,
    p_new_member_id uuid,
    p_role          smallint DEFAULT 1   -- 1 = member, 2 = owner
) RETURNS rw_channel_member
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_channel_owner_id uuid;
    v_existing         rw_channel_member;
    v_inserted         rw_channel_member;
BEGIN
    -- Defense in depth: the function is SECURITY DEFINER (bypasses RLS),
    -- so we re-check the actor matches the inviter. RLS would normally
    -- gate this, but in this function it does not.
    IF p_inviter_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_add_channel_member: inviter mismatch with actor GUC';
    END IF;

    -- Find the channel creator (= owner for groups) or, for direct
    -- channels, any current owner. Use the creator as the owner proxy
    -- because AddMember in Phase 3 is restricted to the channel creator.
    SELECT rw_created_by INTO v_channel_owner_id
    FROM rw_channel
    WHERE rw_id = p_channel_id AND rw_deleted_at IS NULL;

    IF v_channel_owner_id IS NULL THEN
        RAISE EXCEPTION 'rw_add_channel_member: channel % not found', p_channel_id;
    END IF;

    IF v_channel_owner_id <> p_inviter_id THEN
        RAISE EXCEPTION 'rw_add_channel_member: only the channel owner may add members';
    END IF;

    -- Refuse if the new member is already an active member of this channel.
    SELECT * INTO v_existing
    FROM rw_channel_member
    WHERE rw_channel_id = p_channel_id
      AND rw_user_id    = p_new_member_id
      AND rw_left_at   IS NULL;

    IF v_existing.rw_id IS NOT NULL THEN
        RAISE EXCEPTION 'rw_add_channel_member: user is already an active member';
    END IF;

    -- A user can re-join a channel they previously left — close the old
    -- rw_left_at = NULL row by NULLing the left timestamp instead of
    -- inserting a duplicate. The partial unique index uq_rw_channel_member_active
    -- guarantees we cannot have two active rows anyway.
    UPDATE rw_channel_member
       SET rw_left_at = NULL
     WHERE rw_channel_id = p_channel_id
       AND rw_user_id    = p_new_member_id
       AND rw_left_at   IS NOT NULL
    RETURNING * INTO v_inserted;

    IF v_inserted.rw_id IS NOT NULL THEN
        RETURN v_inserted;
    END IF;

    INSERT INTO rw_channel_member (rw_channel_id, rw_user_id, rw_role)
    VALUES (p_channel_id, p_new_member_id, p_role)
    RETURNING * INTO v_inserted;

    RETURN v_inserted;
END;
$$;

GRANT EXECUTE ON FUNCTION rw_add_channel_member(uuid, uuid, uuid, smallint) TO rw_app;
