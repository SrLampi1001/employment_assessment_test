---
name: postgresql-rls-pgvector
description: Design, write, review, and debug the PostgreSQL 18 schema, Row-Level Security policies, pgvector indexes, keyset pagination queries, migrations, and BDD seed data for the Riwi Co. Internal Messaging Platform. Use for ANY work under /db/, any DDL/DML in /backend/migrations/, any query against the rw_* tables (rl_security, vector similarity, full-text search, keyset paging, partial unique indexes), or any change to the RLS security model. The platform's confidentiality guarantee rests on RLS — the DB is the single security boundary — so this skill is required whenever writing or reviewing rw_message / rw_channel / rw_channel_member / rw_user code or queries. Do NOT use for backend Python wiring (use fastapi-development), AI provider integration (use ai-provider-integration), or the React frontend.
---

# PostgreSQL + RLS + pgvector — Riwi Co. Messaging Platform

## Ground rule: the database is the security boundary

Per [`/docs/ARCHITECTURE.md §3`](../docs/ARCHITECTURE.md), the visibility rule is enforced **in PostgreSQL**, not in the application. The backend, the seed script, and even a DBA with `psql` obey the same RLS policies. There is exactly one place to audit.

Consequences for every query you write:

- The application role (`rw_app`) has **no `BYPASSRLS`**. Ever. Granting it would silently break the confidentiality guarantee and is a hard fail per `AGENTS.md`.
- Every request opens **one transaction** and sets `app.current_user_id` via `SELECT set_config(..., true)` (transaction-local). The actor is read from the JWT in middleware — never from the request body.
- **No SQL string concatenation** anywhere. All queries are parameterized (`%s`, `$1`, or `%(name)s`).
- **No physical `DELETE`** on `rw_message` — logical delete via `rw_delete_message(...)`. Same for `rw_channel_member` (`rw_left_at`) and `rw_channel` (`rw_deleted_at`).
- **No `OFFSET` pagination** — keyset only.

## Project baseline (versions + roles + conventions)

| Item | Value | Why |
|---|---|---|
| Postgres | **18** (image `pgvector/pgvector:pg18`) | Current stable; satisfies README's `15+` requirement with headroom. pgvector 0.8.x ships iterative index scans + parallel HNSW. |
| pgvector | **0.8.x** (bundled in the image) | Cosine distance `<=>` operator; HNSW index for ANN |
| Database name | `bd_<nombre>_<apellido>_<clan>` | Per assessment brief |
| Schema | `public` (or a project-specific schema if hosting demands it) | — |
| Table / column prefix | **`rw_`** | Per assessment brief |
| PKs | `uuid` (`gen_random_uuid()` from `pgcrypto`) | No sequential counts leaking through URLs |
| Datetime | `timestamptz` UTC, `DEFAULT now()` | Always UTC; never `timestamp` without TZ |
| Logical delete | `rw_deleted_at` + `rw_deleted_reason` (CHECK that they're both null or both set) | Same pattern as Bioma's `bio_sighting.annulled_at` |
| Application role | `rw_app` (`NOLOGIN`, no `BYPASSRLS`, no `SUPERUSER`) | Owns nothing; uses tables |
| Migration role | `rw_migrator` (DDL only; used by `migrate` container) | Separated from runtime role |
| Search language | `ts_headline('spanish'\|'english', rw_body, plainto_tsquery($1, $2))` | Locale-driven (user's `rw_locale`) |
| Embedding dim | `vector(1024)` — Mistral `mistral-embed` | Pinned by ARCHITECTURE §4.3 |
| GUC for actor | `app.current_user_id` — set via `SELECT set_config('app.current_user_id', $1, true)` | Transaction-local (3rd arg `true`) |

## Step 1: Pin the version, confirm extensions

```bash
docker compose exec db psql -U postgres -d bd_riwi -c "SELECT version();"
docker compose exec db psql -U postgres -d bd_riwi -c "SELECT extname, extversion FROM pg_extension;"
```

Required extensions (`CREATE EXTENSION IF NOT EXISTS` in the initial migration):

- `pgcrypto` — `gen_random_uuid()`
- `vector` (pgvector) — embeddings, HNSW
- `pg_trgm` — optional but useful for fuzzy match
- `unaccent` — optional, for Spanish-aware search normalization

If a managed host's extension allow-list hasn't caught up to PG 18, fall back to PG 17 (still satisfies `15+`).

## Step 2: Schema layout

All files live under `/db/`:

```
db/
├── migrations/
│   ├── 0001_extensions.sql
│   ├── 0010_rls_roles.sql
│   ├── 0020_tables.sql
│   ├── 0030_indexes.sql
│   ├── 0040_functions_procedures.sql
│   ├── 0050_triggers.sql
│   ├── 0060_rls_policies.sql
│   ├── 0070_views.sql
│   └── 0099_seed_test_roles.sql
├── seed/
│   ├── seed.json
│   ├── stg_seed_message.sql   -- Bronze staging table load
│   └── silver_to_gold.sql     -- 3FN load + idempotent re-run
└── tests/
    ├── conftest.py            -- testcontainers fixture
    ├── step_defs/
    └── features/
        └── membership.feature -- the two BDD scenarios from ARCHITECTURE §10
```

Migrations are **forward-only**. Each file is idempotent (`IF NOT EXISTS`). The `migrate` container runs them once at stack boot in lexicographic order.

## Step 3: Tables — names, types, constraints

Follow ARCHITECTURE §2.3 exactly. Highlights:

```sql
-- /db/migrations/0020_tables.sql

CREATE TABLE rw_user (
    rw_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_username     varchar(64)  UNIQUE NOT NULL,
    rw_display_name varchar(120) NOT NULL,
    rw_locale       char(2)      NOT NULL CHECK (rw_locale IN ('es','en')),
    rw_created_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE rw_channel (
    rw_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_name        varchar(120) NOT NULL,
    rw_kind        smallint     NOT NULL CHECK (rw_kind IN (1, 2)),  -- 1=direct, 2=group
    rw_created_by  uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_created_at  timestamptz  NOT NULL DEFAULT now(),
    rw_deleted_at  timestamptz
);

CREATE TABLE rw_channel_member (
    rw_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_channel_id  uuid         NOT NULL REFERENCES rw_channel(rw_id),
    rw_user_id     uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_role        smallint     NOT NULL CHECK (rw_role IN (1, 2)),  -- 1=member, 2=owner
    rw_joined_at   timestamptz  NOT NULL DEFAULT now(),
    rw_left_at     timestamptz,
    CONSTRAINT rw_channel_member_pair UNIQUE (rw_channel_id, rw_user_id)
);

CREATE TABLE rw_message (
    rw_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_channel_id     uuid         NOT NULL REFERENCES rw_channel(rw_id),
    rw_author_id      uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_client_ref     varchar(64),                    -- idempotency key, nullable
    rw_body           text         NOT NULL CHECK (length(rw_body) BETWEEN 1 AND 8000),
    rw_is_edited      boolean      NOT NULL DEFAULT false,
    rw_created_at     timestamptz  NOT NULL DEFAULT now(),
    rw_edited_at      timestamptz,
    rw_deleted_at     timestamptz,
    rw_deleted_reason text,
    rw_embedding      vector(1024),
    CONSTRAINT rw_message_deletion_consistency
        CHECK ((rw_deleted_at IS NULL AND rw_deleted_reason IS NULL)
            OR (rw_deleted_at IS NOT NULL AND rw_deleted_reason IS NOT NULL))
);

CREATE TABLE rw_message_edit (
    rw_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_message_id  uuid         NOT NULL REFERENCES rw_message(rw_id),
    rw_body        text         NOT NULL,
    rw_edited_at   timestamptz  NOT NULL DEFAULT now(),
    rw_editor_id   uuid         NOT NULL REFERENCES rw_user(rw_id)
);

CREATE TABLE rw_message_read (
    rw_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_message_id  uuid         NOT NULL REFERENCES rw_message(rw_id),
    rw_user_id     uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_read_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT rw_message_read_once UNIQUE (rw_message_id, rw_user_id)
);

CREATE TABLE rw_auth_credential (
    rw_user_id        uuid PRIMARY KEY REFERENCES rw_user(rw_id),
    rw_password_hash  text NOT NULL  -- argon2id by the app
);

CREATE TABLE rw_refresh_token (
    rw_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_user_id     uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_token_hash  text         NOT NULL UNIQUE,
    rw_family_id   uuid         NOT NULL,
    rw_expires_at  timestamptz  NOT NULL,
    rw_revoked_at  timestamptz
);

CREATE TABLE rw_copilot_usage (
    rw_id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rw_user_id           uuid         NOT NULL REFERENCES rw_user(rw_id),
    rw_model             varchar(120) NOT NULL,
    rw_prompt_tokens     int          NOT NULL,
    rw_completion_tokens int          NOT NULL,
    rw_cost_usd          numeric(10,6) NOT NULL DEFAULT 0,
    rw_created_at        timestamptz  NOT NULL DEFAULT now()
);
```

### Indexes (ARCHITECTURE §2.4)

```sql
-- /db/migrations/0030_indexes.sql

-- Required partial unique index #1: one ACTIVE membership per (channel, user)
CREATE UNIQUE INDEX uq_rw_channel_member_active
ON rw_channel_member (rw_channel_id, rw_user_id)
WHERE rw_left_at IS NULL;

-- Required partial unique index #2: idempotent sends
CREATE UNIQUE INDEX uq_rw_message_client_ref
ON rw_message (rw_author_id, rw_client_ref)
WHERE rw_client_ref IS NOT NULL;

-- Keyset pagination backing index (per-channel, newest first)
CREATE INDEX ix_rw_message_channel_created
ON rw_message (rw_channel_id, rw_created_at DESC, rw_id DESC);

-- Unread count backing index
CREATE INDEX ix_rw_message_read_user_channel
ON rw_message_read (rw_user_id, rw_message_id);

-- Vector ANN search (HNSW, cosine)
CREATE INDEX ix_rw_message_embedding_hnsw
ON rw_message USING hnsw (rw_embedding vector_cosine_ops);

-- Full-text search backing index (per language)
CREATE INDEX ix_rw_message_body_es ON rw_message
USING gin (to_tsvector('spanish', rw_body));
CREATE INDEX ix_rw_message_body_en ON rw_message
USING gin (to_tsvector('english', rw_body));
```

## Step 4: RLS policies (the heart of the system)

RLS pattern from ARCHITECTURE §3. The actor is set transaction-local; the policy joins to `rw_channel_member` to verify membership.

```sql
-- /db/migrations/0060_rls_policies.sql

ALTER TABLE rw_channel       ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_channel_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_message       ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_message_edit  ENABLE ROW LEVEL SECURITY;
ALTER TABLE rw_message_read  ENABLE ROW LEVEL SECURITY;

-- Read access: actor is a current member of the channel
CREATE POLICY rw_message_visibility ON rw_message
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM rw_channel_member m
        WHERE m.rw_channel_id  = rw_message.rw_channel_id
          AND m.rw_user_id     = current_setting('app.current_user_id', true)::uuid
          AND m.rw_left_at IS NULL
    )
);

-- Insert: same condition; the row must reference a channel the actor is a member of
CREATE POLICY rw_message_insert ON rw_message
FOR INSERT
WITH CHECK (
    EXISTS (
        SELECT 1 FROM rw_channel_member m
        WHERE m.rw_channel_id = rw_message.rw_channel_id
          AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
          AND m.rw_left_at IS NULL
    )
    AND rw_author_id = current_setting('app.current_user_id', true)::uuid
);

-- Update: actor is a member AND is the author (no editing other people's messages)
CREATE POLICY rw_message_update ON rw_message
FOR UPDATE
USING (
    rw_author_id = current_setting('app.current_user_id', true)::uuid
    AND EXISTS (
        SELECT 1 FROM rw_channel_member m
        WHERE m.rw_channel_id = rw_message.rw_channel_id
          AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
          AND m.rw_left_at IS NULL
    )
)
WITH CHECK (rw_deleted_at IS NULL);   -- logical delete goes through the procedure

-- rw_message_edit inherits the same membership check
CREATE POLICY rw_message_edit_visibility ON rw_message_edit
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM rw_message msg
        JOIN rw_channel_member m
          ON m.rw_channel_id = msg.rw_channel_id
         AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
         AND m.rw_left_at IS NULL
        WHERE msg.rw_id = rw_message_edit.rw_message_id
    )
);

-- rw_message_read: an actor reads/marks messages in channels they're a member of
CREATE POLICY rw_message_read_visibility ON rw_message_read
FOR ALL
USING (
    rw_user_id = current_setting('app.current_user_id', true)::uuid
);

-- rw_channel_member: actor sees their own memberships; can leave (set rw_left_at)
CREATE POLICY rw_channel_member_self ON rw_channel_member
FOR ALL
USING (rw_user_id = current_setting('app.current_user_id', true)::uuid);

-- rw_channel: actor sees channels they're a current member of
CREATE POLICY rw_channel_visibility ON rw_channel
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM rw_channel_member m
        WHERE m.rw_channel_id = rw_channel.rw_id
          AND m.rw_user_id    = current_setting('app.current_user_id', true)::uuid
          AND m.rw_left_at IS NULL
    )
);
```

**Two important details:**

1. **`current_setting('app.current_user_id', true)` — note the `true` second argument.** It returns `NULL` (instead of erroring) when the GUC is unset. Cast `::uuid` then fails closed (returns zero rows) instead of failing open. This is the difference between a security model and a security incident.
2. **Privileges for the app role.** `GRANT SELECT, INSERT, UPDATE, DELETE ON rw_* TO rw_app;` — RLS is the *row-level* filter; standard `GRANT` is still needed at the table level. The default-deny policy applies if RLS is enabled with no matching `FOR SELECT` policy.

## Step 5: Transactional functions and procedures

The write path goes through DB functions/procedures, not raw application SQL (ARCHITECTURE §3 + §5.1).

```sql
-- /db/migrations/0040_functions_procedures.sql

CREATE OR REPLACE FUNCTION rw_register_user(
    p_username      varchar,
    p_display_name  varchar,
    p_locale        char(2),
    p_password_hash text
) RETURNS uuid
LANGUAGE plpgsql
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
$$ SECURITY DEFINER;

CREATE OR REPLACE FUNCTION rw_send_message(
    p_channel_id  uuid,
    p_author_id   uuid,
    p_body        text,
    p_client_ref  varchar DEFAULT NULL
) RETURNS rw_message
LANGUAGE plpgsql
AS $$
DECLARE
    v_msg rw_message;
BEGIN
    INSERT INTO rw_message (rw_channel_id, rw_author_id, rw_client_ref, rw_body)
    VALUES (p_channel_id, p_author_id, p_client_ref, p_body)
    ON CONFLICT (rw_author_id, rw_client_ref)
        WHERE rw_client_ref IS NOT NULL
        DO NOTHING
    RETURNING * INTO v_msg;

    IF v_msg.rw_id IS NULL THEN
        SELECT * INTO v_msg FROM rw_message
        WHERE rw_author_id = p_author_id AND rw_client_ref = p_client_ref;
    END IF;

    RETURN v_msg;
END;
$$ SECURITY DEFINER;

CREATE OR REPLACE PROCEDURE rw_edit_message(
    p_message_id uuid,
    p_editor_id  uuid,
    p_new_body   text
)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO rw_message_edit (rw_message_id, rw_body, rw_editor_id)
    VALUES (p_message_id, p_new_body, p_editor_id);

    UPDATE rw_message
       SET rw_body = p_new_body,
           rw_is_edited = true,
           rw_edited_at = now()
     WHERE rw_id = p_message_id;
END;
$$ SECURITY DEFINER;

CREATE OR REPLACE PROCEDURE rw_delete_message(
    p_message_id uuid,
    p_actor_id   uuid,
    p_reason     text
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE rw_message
       SET rw_deleted_at = now(),
           rw_deleted_reason = p_reason
     WHERE rw_id = p_message_id
       AND rw_author_id = p_actor_id;
END;
$$ SECURITY DEFINER;
```

Why `SECURITY DEFINER`? Because the procedure runs with the privileges of the function owner (the migrator role). It still goes through RLS checks for the actor's *row* visibility — but it can insert into `rw_message_edit` (which an unprivileged role could be restricted from doing directly). The combination is "RLS filters which rows are visible, SECURITY DEFINER lets the trusted DB function modify them on behalf of the actor".

## Step 6: Triggers — keeping `rw_embedding` in lockstep

```sql
-- /db/migrations/0050_triggers.sql

CREATE OR REPLACE FUNCTION rw_compute_message_embedding() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Embedding is computed in the application layer and passed via the INSERT/UPDATE.
    -- This trigger only validates that the row was provided one if the body is non-empty.
    -- If you prefer server-side embedding, swap the body of this trigger for a call to
    -- an `EmbeddingProvider` HTTP extension (e.g. pg_net) — but that ties the DB to a
    -- network call, which is usually the wrong trade.
    IF NEW.rw_body IS NOT NULL AND NEW.rw_embedding IS NULL THEN
        RAISE WARNING 'rw_message inserted without embedding; copilot search will skip it';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_message_embedding
AFTER INSERT OR UPDATE OF rw_body ON rw_message
FOR EACH ROW EXECUTE FUNCTION rw_compute_message_embedding();
```

The project chose to compute embeddings in the application (`infrastructure/ai/MistralAdapter`) and pass them in via the `rw_send_message` parameter list. The trigger exists as a guardrail — `RAISE WARNING` if a row landed without one, so the seed script can't silently skip the embed step.

## Step 7: Views

```sql
-- /db/migrations/0070_views.sql

CREATE VIEW rw_visible_message AS
SELECT * FROM rw_message WHERE rw_deleted_at IS NULL;

-- RLS still applies on top of the view (Postgres honours RLS on the underlying table).
-- This view just collapses the "is it logically deleted?" check so query authors don't have to remember.

---

## Step 8: Keyset pagination SQL (no `OFFSET`)

```sql
-- First page (no cursor)
SELECT * FROM rw_visible_message
WHERE rw_channel_id = $1
ORDER BY rw_created_at DESC, rw_id DESC
LIMIT $2;

-- Subsequent pages
SELECT * FROM rw_visible_message
WHERE rw_channel_id = $1
  AND (rw_created_at, rw_id) < ($2::timestamptz, $3::uuid)
ORDER BY rw_created_at DESC, rw_id DESC
LIMIT $4;
```

Backed by the composite index `(rw_channel_id, rw_created_at DESC, rw_id DESC)`. `OFFSET` is banned (`ARCHITECTURE.md §6`) because it scans-and-discards rows and skips/repeats when the list mutates between pages.

## Step 9: Vector similarity + lexical search (the copilot)

```sql
-- Semantic (cosine, HNSW)
SELECT rw_id, rw_channel_id, rw_body, rw_embedding <=> $1 AS distance
FROM rw_visible_message
ORDER BY rw_embedding <=> $1
LIMIT $2;

-- Lexical + highlight (ARCHITECTURE §4.2)
SELECT rw_id,
       rw_channel_id,
       ts_headline(rw_locale, rw_body, plainto_tsquery(rw_locale, $1),
                   'StartSel=<mark>, StopSel=</mark>') AS snippet
FROM rw_visible_message
WHERE to_tsvector(rw_locale, rw_body) @@ plainto_tsquery(rw_locale, $1)
ORDER BY rw_created_at DESC
LIMIT $2;
```

Both queries run under the same `app.current_user_id` RLS context, so non-members get zero rows by construction.

## Step 10: Seed / Bronze–Silver (ARCHITECTURE §9)

Even for a one-shot load, Bronze keeps the seed as received (auditable, immutable). Silver is the 3FN model.

```sql
-- /db/migrations/0091_bronze_staging.sql
CREATE TABLE stg_seed_message (
    rw_payload jsonb NOT NULL,
    rw_loaded_at timestamptz NOT NULL DEFAULT now()
);

-- /db/seed/stg_seed_message.sql
\copy stg_seed_message (rw_payload) FROM '/seed/seed.json' WITH (FORMAT json);
```

Silver load uses parameterized inserts (or `COPY` for large bodies). Embeddings are computed in Python (`infrastructure/ai/MistralAdapter`) and inserted in batches of up to 512 texts per `embeddings.create(...)` call — the Mistral free tier's batch limit.

## Step 11: Testing — BDD against real PostgreSQL

Use **`testcontainers-python`** so tests exercise the *real* `pgvector` extension and the *real* RLS policy. Mocking RLS defeats the point.

```python
# /db/tests/conftest.py
import pytest, uuid
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("pgvector/pgvector:pg18") as pg:
        url = pg.get_connection_url()
        # Run migrations once at session scope
        run_migrations(url)
        yield url

@pytest.fixture
def conn(pg_url):
    import psycopg
    with psycopg.connect(pg_url) as c, c.cursor() as cur:
        yield cur

def as_actor(cur, actor_id: uuid.UUID):
    cur.execute("SELECT set_config('app.current_user_id', %s, true)", (str(actor_id),))
```

The two mandatory BDD scenarios from `ARCHITECTURE §10` live here. They MUST pass against the real `pgvector/pgvector:pg18` image and the real `rw_app` role (no `BYPASSRLS`).

## Step 12: Project-banned SQL patterns

These are **hard fails** in PR review, not style nits. Source: `AGENTS.md` + `ARCHITECTURE.md`.

| Banned | Use instead |
|---|---|
| `GRANT BYPASSRLS TO rw_app`, `ALTER ROLE rw_app SUPERUSER`, `SET ROLE bypassrls` | Keep `rw_app` plain; security is RLS + GUC actor |
| `f"SELECT * FROM rw_message WHERE id = {x}"` | `SELECT * FROM rw_message WHERE id = %s` with `cur.execute(sql, (x,))` |
| `DELETE FROM rw_message WHERE id = ...` | `CALL rw_delete_message(...)` (procedure) |
| `LIMIT N OFFSET M` | Keyset `WHERE (created_at, id) < ($cursor_ts, $cursor_id) ORDER BY ... LIMIT N` |
| `RAISE NOTICE` / `RAISE INFO` for security warnings | `RAISE WARNING` (PG logs them at WARNING level by default) |
| Hardcoding model names in seed SQL | Embeddings are computed in the app and passed as parameters |
| `REVOKE` followed by `GRANT` of `BYPASSRLS` for a one-off admin task | Do the task as `rw_migrator`, not as `rw_app` |
| `SELECT *` in production queries | Explicit column list — schemas change; `*` is a footgun |
| `psql -c "UPDATE rw_user SET ...; SELECT pg_sleep(60);"` style long-lived transactions from migrations | Keep migrations short; long work goes in a one-shot `Procedure` |

## Step 13: Where to go next

- For the Python-side RLS enforcement (psycopg transaction, GUC setting, middleware), use the `fastapi-development` skill.
- For embedding / chat model wiring behind the `EmbeddingProvider` / `ChatProvider` ports, use the `ai-provider-integration` skill.
- For the BDD feature files and pytest-bdd step definitions, use the `pytest-bdd-testcontainers` skill.
- For architectural questions, the source of truth is `/docs/ARCHITECTURE.md`. If this skill and the architecture disagree, **the architecture wins**.