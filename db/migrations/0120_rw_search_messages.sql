-- Phase 5 — 0120_rw_search_messages.sql
-- Lexical search with highlight (ARCHITECTURE §4.2, §11.2) +
-- per-channel unread count (ARCHITECTURE §8) + bulk mark-as-read
-- (ARCHITECTURE §6 — POST /api/v1/messages/{id}/read already exists
-- for the single-message case from Phase 4).
--
-- Three SECURITY DEFINER objects:
--
--   rw_search_messages(p_channel_id, p_query, p_limit, p_actor_id)
--     → TABLE(rw_id, rw_channel_id, rw_author_id, rw_body,
--             rw_created_at, rw_highlight)
--     Uses ts_headline(rw_locale, rw_body, plainto_tsquery(...)) so the
--     highlight tag set is `<mark>…</mark>`. Locale is taken from the
--     actor's rw_locale — NOT hardcoded to 'spanish' or 'english'.
--
--   rw_unread_count_for_channel(p_channel_id, p_user_id) → integer
--     Counts visible (rw_deleted_at IS NULL) messages that the user
--     has NOT marked read in rw_message_read. Returns 0 for non-members.
--
--   rw_mark_channel_read(p_channel_id, p_user_id) → integer
--     Inserts a rw_message_read row for every visible message that
--     isn't already marked, in one statement. Idempotent (the
--     UNIQUE constraint on (rw_message_id, rw_user_id) makes a
--     re-mark a no-op).
--
-- SECURITY DEFINER defense in depth:
--   The function owner is the migrator (superuser) → RLS does NOT
--   apply inside the function body even though `app.current_user_id`
--   is set (the GUC just won't be consulted). Each function therefore
--   re-checks:
--     1. `p_actor_id = current_setting('app.current_user_id', true)::uuid`
--     2. The actor is a CURRENT member of p_channel_id (LEFT NULL = fail)
--   so a non-member gets zero rows even if they bypass the application.
--
-- The Phase 4 OUT-parameter naming gotcha (out_ prefix) is applied here
-- so columns and parameters never collide in plpgsql.

-- ─── rw_search_messages ─────────────────────────────────────────────────
DROP FUNCTION IF EXISTS rw_search_messages(uuid, text, integer, uuid);

CREATE FUNCTION rw_search_messages(
    p_channel_id  uuid,
    p_query       text,
    p_limit       integer,
    p_actor_id    uuid
) RETURNS TABLE (
    out_rw_id           uuid,
    out_rw_channel_id   uuid,
    out_rw_author_id    uuid,
    out_rw_body         text,
    out_rw_created_at   timestamptz,
    out_rw_highlight    text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
AS $$
DECLARE
    v_locale  text;  -- 'spanish' / 'english' — text matches the
                    -- regconfig overload of plainto_tsquery / to_tsvector
                    -- (char(2) doesn't resolve to that overload).
BEGIN
    IF p_actor_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_search_messages: actor mismatch with GUC';
    END IF;

    -- Defense in depth #1: RLS would normally filter rows the actor
    -- can't see, but SECURITY DEFINER runs as the function owner
    -- (superuser in this case) and BYPASSES RLS. Re-check membership.
    IF NOT EXISTS (
        SELECT 1 FROM rw_channel_member
        WHERE rw_channel_id = p_channel_id
          AND rw_user_id    = p_actor_id
          AND rw_left_at   IS NULL
    ) THEN
        RETURN;  -- non-member gets an empty result set
    END IF;

    -- Pull the locale from the DB, not the client. This is the
    -- human-review check from issue #9 — locale MUST come from
    -- rw_user.rw_locale, not be hardcoded. We accept the stored
    -- 'es' / 'en' and expand to the full regconfig name; an unknown
    -- value falls back to 'simple' so a malformed locale doesn't
    -- 500 the whole search.
    SELECT CASE rw_locale
             WHEN 'es' THEN 'spanish'
             WHEN 'en' THEN 'english'
             ELSE          'simple'
           END
      INTO v_locale
      FROM rw_user
     WHERE rw_id = p_actor_id;

    IF v_locale IS NULL THEN
        RAISE EXCEPTION 'rw_search_messages: actor locale not found';
    END IF;

    -- ts_headline returns the body with <mark>…</mark> around matches.
    RETURN QUERY
    SELECT  m.rw_id,
            m.rw_channel_id,
            m.rw_author_id,
            m.rw_body,
            m.rw_created_at,
            ts_headline(
                v_locale::regconfig,
                m.rw_body,
                plainto_tsquery(v_locale::regconfig, p_query),
                'StartSel=<mark>, StopSel=</mark>'
            ) AS rw_highlight
    FROM    rw_message m
    WHERE   m.rw_channel_id = p_channel_id
      AND   m.rw_deleted_at IS NULL
      AND   to_tsvector(v_locale::regconfig, m.rw_body)
              @@ plainto_tsquery(v_locale::regconfig, p_query)
    ORDER BY m.rw_created_at DESC
    LIMIT p_limit;
END;
$$;

-- ─── rw_unread_count_for_channel ────────────────────────────────────────
DROP FUNCTION IF EXISTS rw_unread_count_for_channel(uuid, uuid);

CREATE FUNCTION rw_unread_count_for_channel(
    p_channel_id  uuid,
    p_user_id     uuid
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
AS $$
DECLARE
    v_count integer;
BEGIN
    IF p_user_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_unread_count_for_channel: user mismatch with GUC';
    END IF;

    -- Defense in depth: same as above — SECURITY DEFINER bypasses RLS.
    IF NOT EXISTS (
        SELECT 1 FROM rw_channel_member
        WHERE rw_channel_id = p_channel_id
          AND rw_user_id    = p_user_id
          AND rw_left_at   IS NULL
    ) THEN
        RETURN 0;  -- non-members get zero unread
    END IF;

    SELECT count(*) INTO v_count
    FROM   rw_visible_message m
    WHERE  m.rw_channel_id = p_channel_id
      AND  NOT EXISTS (
          SELECT 1 FROM rw_message_read r
          WHERE  r.rw_message_id = m.rw_id
            AND  r.rw_user_id    = p_user_id
      );

    RETURN v_count;
END;
$$;

-- ─── rw_mark_channel_read ───────────────────────────────────────────────
-- Inserts (message_id, user_id) for every visible message in the channel
-- that isn't already marked read. One statement; idempotent thanks to
-- the UNIQUE constraint on (rw_message_id, rw_user_id). Returns the
-- number of rows actually inserted (useful for the API + tests).
DROP FUNCTION IF EXISTS rw_mark_channel_read(uuid, uuid);

CREATE FUNCTION rw_mark_channel_read(
    p_channel_id  uuid,
    p_user_id     uuid
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_inserted integer;
BEGIN
    IF p_user_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid THEN
        RAISE EXCEPTION 'rw_mark_channel_read: user mismatch with GUC';
    END IF;

    -- Defense in depth: a non-member MUST NOT be able to insert
    -- rw_message_read rows for messages they can't see (would pollute
    -- the table + leak their interest via FK counts).
    IF NOT EXISTS (
        SELECT 1 FROM rw_channel_member
        WHERE rw_channel_id = p_channel_id
          AND rw_user_id    = p_user_id
          AND rw_left_at   IS NULL
    ) THEN
        RETURN 0;
    END IF;

    WITH new_reads AS (
        INSERT INTO rw_message_read (rw_message_id, rw_user_id)
        SELECT m.rw_id, p_user_id
        FROM   rw_visible_message m
        WHERE  m.rw_channel_id = p_channel_id
          AND  NOT EXISTS (
              SELECT 1 FROM rw_message_read r
              WHERE  r.rw_message_id = m.rw_id
                AND  r.rw_user_id    = p_user_id
          )
        ON CONFLICT (rw_message_id, rw_user_id) DO NOTHING
        RETURNING rw_id
    )
    SELECT count(*) INTO v_inserted FROM new_reads;

    RETURN v_inserted;
END;
$$;

-- ─── Grants ─────────────────────────────────────────────────────────────
GRANT EXECUTE ON FUNCTION rw_search_messages(uuid, text, integer, uuid)
    TO rw_app;
GRANT EXECUTE ON FUNCTION rw_unread_count_for_channel(uuid, uuid)
    TO rw_app;
GRANT EXECUTE ON FUNCTION rw_mark_channel_read(uuid, uuid)
    TO rw_app;