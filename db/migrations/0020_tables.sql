-- Phase 1 — 0020_tables.sql
-- All rw_* tables in 3FN. See ARCHITECTURE.md §2.3 (ER diagram) and
-- .agents/skills/postgresql-rls-pgvector (Step 3) for the baseline.
--
-- Idempotent: uses IF NOT EXISTS so the file is safe to re-run on a
-- container that already has the schema. RLS policies (0060) and the
-- view (0070) live in their own files because they need different
-- idempotency patterns.

-- 1. Independent entities (no FKs to other rw_* tables)

CREATE TABLE IF NOT EXISTS rw_user (
    rw_id            uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_username      varchar(64)   NOT NULL UNIQUE,
    rw_display_name  varchar(120)  NOT NULL,
    rw_locale        char(2)       NOT NULL CHECK (rw_locale IN ('es', 'en')),
    rw_created_at    timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rw_auth_credential (
    rw_user_id        uuid  PRIMARY KEY REFERENCES rw_user(rw_id) ON DELETE CASCADE,
    rw_password_hash  text  NOT NULL
);

CREATE TABLE IF NOT EXISTS rw_copilot_usage (
    rw_id                 uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_user_id            uuid           NOT NULL REFERENCES rw_user(rw_id),
    rw_model              varchar(120)   NOT NULL,
    rw_prompt_tokens      int            NOT NULL,
    rw_completion_tokens  int            NOT NULL,
    rw_cost_usd           numeric(10, 6) NOT NULL DEFAULT 0,
    rw_created_at         timestamptz    NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rw_refresh_token (
    rw_id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_user_id     uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_token_hash  text         NOT NULL UNIQUE,
    rw_family_id   uuid         NOT NULL,
    rw_expires_at  timestamptz  NOT NULL,
    rw_revoked_at  timestamptz
);

-- 2. Channels (depends on rw_user)

CREATE TABLE IF NOT EXISTS rw_channel (
    rw_id          uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_name        varchar(120)  NOT NULL,
    rw_kind        smallint      NOT NULL CHECK (rw_kind IN (1, 2)),  -- 1=direct, 2=group
    rw_created_by  uuid          NOT NULL REFERENCES rw_user(rw_id),
    rw_created_at  timestamptz   NOT NULL DEFAULT now(),
    rw_deleted_at  timestamptz
);

-- 3. Membership (depends on rw_channel, rw_user)

CREATE TABLE IF NOT EXISTS rw_channel_member (
    rw_id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_channel_id  uuid         NOT NULL REFERENCES rw_channel(rw_id),
    rw_user_id     uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_role        smallint     NOT NULL CHECK (rw_role IN (1, 2)),  -- 1=member, 2=owner
    rw_joined_at   timestamptz  NOT NULL DEFAULT now(),
    rw_left_at     timestamptz
);

-- 4. Messages (depends on rw_channel, rw_user)

CREATE TABLE IF NOT EXISTS rw_message (
    rw_id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_channel_id      uuid          NOT NULL REFERENCES rw_channel(rw_id),
    rw_author_id       uuid          NOT NULL REFERENCES rw_user(rw_id),
    rw_client_ref      varchar(64),                      -- idempotency key, nullable
    rw_body            text          NOT NULL CHECK (length(rw_body) BETWEEN 1 AND 8000),
    rw_is_edited       boolean       NOT NULL DEFAULT false,
    rw_created_at      timestamptz   NOT NULL DEFAULT now(),
    rw_edited_at       timestamptz,
    rw_deleted_at      timestamptz,
    rw_deleted_reason  text,
    rw_embedding       vector(1024),
    -- Logical-deletion invariant: either both fields are NULL, or both are set.
    CONSTRAINT rw_message_deletion_consistency
        CHECK (
            (rw_deleted_at IS NULL     AND rw_deleted_reason IS NULL)
         OR (rw_deleted_at IS NOT NULL AND rw_deleted_reason IS NOT NULL)
        )
);

-- 5. Append-only edit history (depends on rw_message, rw_user)

CREATE TABLE IF NOT EXISTS rw_message_edit (
    rw_id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_message_id  uuid         NOT NULL REFERENCES rw_message(rw_id),
    rw_body        text         NOT NULL,
    rw_edited_at   timestamptz  NOT NULL DEFAULT now(),
    rw_editor_id   uuid         NOT NULL REFERENCES rw_user(rw_id)
);

-- 6. Read receipts (depends on rw_message, rw_user)

CREATE TABLE IF NOT EXISTS rw_message_read (
    rw_id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_message_id  uuid         NOT NULL REFERENCES rw_message(rw_id),
    rw_user_id     uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_read_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT rw_message_read_once UNIQUE (rw_message_id, rw_user_id)
);
