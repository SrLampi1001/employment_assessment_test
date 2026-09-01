---
name: postgresql-rls-pgvector
description: Design, write, review, and debug the PostgreSQL 18 schema, Row-Level Security policies, pgvector indexes, keyset pagination queries, migrations, and BDD seed data for the Riwi Co. Internal Messaging Platform. Use for ANY work under /db/, any DDL/DML in /backend/migrations/, any query against the rw_* tables (rl_security, vector similarity, full-text search, keyset paging, partial unique indexes), or any change to the RLS security model. The platform's confidentiality guarantee rests on RLS — the DB is the single security boundary — so this skill is required whenever writing or reviewing rw_message / rw_channel / rw_channel_member / rw_user code or queries. Do NOT use for backend Python wiring (use fastapi-development), AI provider integration (use ai-provider-integration), or the React frontend.
---

# PostgreSQL + RLS + pgvector — Riwi Co. Messaging Platform

> **Skill maintenance notice (verified 2026-08-29).** Per
> [`/AGENTS.md`](../AGENTS.md) "Skill Maintenance": this skill no
> longer carries predictive code blocks — every code path below
> references the shipped migration file directly. If this file
> contradicts `/db/migrations/*.sql`, the migrations win.

## Ground rule: the database is the security boundary

Per [`/docs/ARCHITECTURE.md §3`](../docs/ARCHITECTURE.md), the visibility rule is enforced **in PostgreSQL**, not in the application. The backend, the seed script, and even a DBA with `psql` obey the same RLS policies. There is exactly one place to audit.

Consequences for every query you write:

- The application role (`rw_app` / `rw_app_login`) has **no `BYPASSRLS`**. Ever. Granting it would silently break the confidentiality guarantee and is a hard fail per `AGENTS.md`.
- Every request opens **one transaction** and sets `app.current_user_id` via `SELECT set_config(..., true)` (transaction-local). The actor is read from the JWT in middleware — never from the request body.
- **No SQL string concatenation** anywhere. All queries are parameterized (`%s`, `$1`, or `%(name)s`).
- **No physical `DELETE`** on `rw_message` — logical delete via `rw_delete_message(...)`. Same for `rw_channel_member` (`rw_left_at`) and `rw_channel` (`rw_deleted_at`).
- **No `OFFSET` pagination** — keyset only.

## Project baseline (versions + roles + conventions)

| Item | Value | Why |
|---|---|---|
| Postgres | **18** (image `pgvector/pgvector:pg18`) | Current stable; satisfies README's `15+` requirement with headroom. pgvector 0.8.x ships iterative index scans + parallel HNSW. |
| pgvector | **0.8.x** (bundled in the image) | Cosine distance `<=>` operator; HNSW index for ANN |
| Database name | `bd_<nombre>_<apellido>_<clan>` (actual project value: `db_santiago_sanchez_nakamoto`) | Per assessment brief |
| Schema | `public` | — |
| Table / column prefix | **`rw_`** | Per assessment brief |
| PKs | `uuid` (`gen_random_uuid()` from `pgcrypto`) | No sequential counts leaking through URLs |
| Datetime | `timestamptz` UTC, `DEFAULT now()` | Always UTC; never `timestamp` without TZ |
| Logical delete | `rw_deleted_at` + `rw_deleted_reason` (CHECK that they're both null or both set) | Same pattern as Bioma's `bio_sighting.annulled_at` |
| Application role | `rw_app` (`NOLOGIN`, no `BYPASSRLS`, no `SUPERUSER`); `rw_app_login` (`IN ROLE rw_app`) is the runtime login role | Per `0002_roles.sql` |
| Migration role | `rw_migrator` (DDL only; used by `migrate` container) | Separated from runtime role |
| Search language | `ts_headline('spanish'\|'english', rw_body, plainto_tsquery($1, $2))` | Locale-driven (user's `rw_locale`) |
| Embedding dim | `vector(1024)` — Mistral `mistral-embed` | Pinned by ARCHITECTURE §4.3 |
| GUC for actor | `app.current_user_id` — set via `SELECT set_config('app.current_user_id', $1, true)` | Transaction-local (3rd arg `true`) |

## Step 1: Pin the version, confirm extensions

```bash
docker compose exec db psql -U postgres -d bd_<your_db> -c "SELECT version();"
docker compose exec db psql -U postgres -d bd_<your_db> -c "SELECT extname, extversion FROM pg_extension;"
```

Required extensions (`CREATE EXTENSION IF NOT EXISTS` in the initial migration):

- `pgcrypto` — `gen_random_uuid()`
- `vector` (pgvector) — embeddings, HNSW
- `pg_trgm` — optional but useful for fuzzy match
- `unaccent` — optional, for Spanish-aware search normalization

If a managed host's extension allow-list hasn't caught up to PG 18, fall back to PG 17 (still satisfies `15+`).

## Step 2: Project layout (verified 2026-08-29)

The actual shipped layout is:

```
db/migrations/
├── 0001_extensions.sql            — pgcrypto + vector + (optional pg_trgm/unaccent)
├── 0002_roles.sql                 — rw_migrator + rw_app + rw_app_login
├── 0020_tables.sql                — 9 rw_* tables in 3FN dependency order
├── 0030_indexes.sql               — partial unique + keyset + HNSW + GIN FTS + unread
├── 0040_functions_procedures.sql  — rw_register_user, rw_create_channel, rw_send_message + rw_edit_message, rw_delete_message procedures
├── 0050_triggers.sql              — trg_message_embedding (RAISE WARNING if NULL)
├── 0060_rls_policies.sql          — RLS on rw_channel / rw_channel_member / rw_message / rw_message_edit / rw_message_read
├── 0070_views.sql                 — rw_visible_message (WITH (security_invoker = true))
├── 0080_grants.sql                — table-level GRANTs to rw_app (no rw_refresh_token / rw_copilot_usage — those go through SECURITY DEFINER functions)
├── 0090_bronze_staging.sql        — stg_seed_message (jsonb)
├── 0100_rw_add_channel_member.sql — rw_add_channel_member(...) SECURITY DEFINER (Phase 3)
├── 0110_rw_send_message_replay_flag.sql — adds out_was_replay OUT param (Phase 4)
├── 0120_rw_search_messages.sql    — rw_search_messages + rw_unread_count_for_channel + rw_mark_channel_read (Phase 5)
├── 0130_rw_message_read_channel_id.sql — adds rw_channel_id + trigger + ix_rw_message_read_user_channel (Phase 7)
└── 0140_rls_on_user_scoped_tables.sql — RLS on rw_refresh_token + rw_copilot_usage + their SECURITY DEFINER wrappers (Phase 7)

backend/scripts/seed.py             — Bronze → Silver loader (psycopg + Mistral batched embed)
backend/tests/conftest.py           — testcontainers fixture + RLS-aware connections
backend/tests/features/             — Gherkin feature files (auth, channels, messages, membership, copilot, search, rls_isolation)
backend/tests/step_defs/            — One .py per .feature (test_*.py)
```

Migrations are **forward-only**. Each file is idempotent (`IF NOT EXISTS` / `CREATE OR REPLACE` / `DROP IF EXISTS`). The `migrate` compose service runs them once at stack boot in lexicographic order.

> **What does NOT exist** (do not invent references to these files):
> `0010_rls_roles.sql` — roles live in `0002_roles.sql`.
> `db/seed/seed.json`, `db/seed/stg_seed_message.sql`, `db/seed/silver_to_gold.sql` — the seed is `backend/scripts/seed.py` (loads `seed.json` via `psycopg` directly, not a separate `seed.json` SQL file in `db/`).
> `db/tests/` — BDD tests live under `backend/tests/`.

## Step 3: Tables + indexes — see shipped files

- [`/db/migrations/0020_tables.sql`](../../db/migrations/0020_tables.sql) — one-line summary: 9 `rw_*` tables in 3FN dependency order; every table `CREATE TABLE IF NOT EXISTS`; the `rw_message_deletion_consistency` CHECK invariant on logical deletion (both columns null or both set).
- [`/db/migrations/0030_indexes.sql`](../../db/migrations/0030_indexes.sql) — one-line summary: the two REQUIRED partial unique indexes (`uq_rw_channel_member_active`, `uq_rw_message_client_ref`), the keyset pagination backing index `(rw_channel_id, rw_created_at DESC, rw_id DESC)`, the HNSW `vector_cosine_ops` index, per-locale GIN FTS indexes, and the `ix_rw_message_read_user_channel` index (per ARCH §2.4, `(rw_user_id, rw_channel_id)` — the column was added by migration 0130).

The names follow ARCH §2.3 exactly (`snake_case`, `rw_` prefix, `uuid` PKs, `timestamptz` UTC with `DEFAULT now()`). Re-run safely — `IF NOT EXISTS` makes the migrations idempotent.

## Step 4: RLS policies (the heart of the system)

RLS pattern from ARCH §3. The actor is set transaction-local; the policy joins to `rw_channel_member` to verify membership.

**See the shipped file:** [`/db/migrations/0060_rls_policies.sql`](../../db/migrations/0060_rls_policies.sql) — one-line summary: RLS is enabled on `rw_channel / rw_channel_member / rw_message / rw_message_edit / rw_message_read`; per-user policies split SELECT / INSERT / UPDATE / ALL; every policy reads the actor from `current_setting('app.current_user_id', true)::uuid` and joins to `rw_channel_member` to verify the actor is a current member of the channel. Idempotent — each policy is `DROP POLICY IF EXISTS` then `CREATE POLICY`.

For `rw_refresh_token` and `rw_copilot_usage`, see [`/db/migrations/0140_rls_on_user_scoped_tables.sql`](../../db/migrations/0140_rls_on_user_scoped_tables.sql) — same `rw_user_id = GUC` policy pattern, but the runtime role has **only EXECUTE on the SECURITY DEFINER functions** (`rw_insert_refresh_token` / `rw_find_refresh_token` / `rw_revoke_refresh_token` / `rw_revoke_refresh_token_family` / `rw_record_copilot_usage`). The rationale for the SECURITY DEFINER wrapper is documented in `DECISIONS.md`.

**Two important details:**

1. **`current_setting('app.current_user_id', true)` — note the `true` second argument.** It returns `NULL` (instead of erroring) when the GUC is unset. Cast `::uuid` then fails closed (returns zero rows) instead of failing open. This is the difference between a security model and a security incident. *Known edge case:* an explicitly set empty string (e.g. `set_config(..., NULL, false)` from a test) errors on the `::uuid` cast — still fail-closed (no leak) but ugly. Wrap in `NULLIF(setting, '')` if a cleaner error is needed.
2. **Privileges for the app role.** [`/db/migrations/0080_grants.sql`](../../db/migrations/0080_grants.sql) issues `GRANT SELECT, INSERT, UPDATE, DELETE ON rw_* TO rw_app` + `GRANT EXECUTE ON FUNCTION/PROCEDURE ...`. RLS is the *row-level* filter; standard `GRANT` is still needed at the table level for the tables the runtime can touch directly (every `rw_*` except `rw_refresh_token`, where the runtime has only the SECURITY DEFINER functions; `rw_copilot_usage` retains `SELECT` for the §11.4 summary endpoint). The default-deny policy applies if RLS is enabled with no matching `FOR SELECT` policy.

### 4.5: Channel-scoped RLS — the Phase 3 pattern

The `rw_channel_member` policy is intentionally narrow — the actor can only see / insert / update **their own** membership rows (`rw_user_id = GUC`). That lets the actor:

- See their own role in a channel.
- `UPDATE` their own `rw_left_at` for the leave flow.
- `INSERT` a membership row for themselves (re-join after leaving).

But it **forbids** adding a *different* user as a member, so the "channel owner invites someone else" flow needs a SECURITY DEFINER function. See [`/db/migrations/0100_rw_add_channel_member.sql`](../../db/migrations/0100_rw_add_channel_member.sql) for the shipped pattern. The function body enforces:

1. The GUC actor matches the inviter (`rw_add_channel_member: inviter mismatch with actor GUC`).
2. The inviter is the channel creator (`rw_created_by = p_inviter_id`).
3. The new member is not already an active member (`rw_left_at IS NULL`).
4. Re-joins NULL the prior `rw_left_at` instead of inserting a duplicate — the `uq_rw_channel_member_active` partial unique index (`Step 3` referenced it) would reject a duplicate active row anyway.

`rw_create_channel` (Phase 1, 0040) inserts the channel + the creator's `owner` membership in one statement. `ListVisibleChannels` at the use case level is a plain `SELECT FROM rw_channel JOIN rw_channel_member` — the join to `rw_channel_member` is RLS-filtered to the actor's own rows, so each channel row gets at most one matching membership row (the actor's own). No `EXISTS` filter needed at the application layer.

## Step 5: Transactional functions and procedures

The write path goes through DB functions/procedures, not raw application SQL (ARCH §3 + §5.1).

**See the shipped file:** [`/db/migrations/0040_functions_procedures.sql`](../../db/migrations/0040_functions_procedures.sql) — one-line summary: 3 functions (`rw_register_user`, `rw_create_channel`, `rw_send_message`) and the 2 REQUIRED procedures (`rw_edit_message`, `rw_delete_message`), all `LANGUAGE plpgsql SECURITY DEFINER`. Each function checks `p_actor_id = current_setting('app.current_user_id', true)::uuid` and re-verifies membership explicitly (defense in depth: the function is `SECURITY DEFINER`, so RLS does *not* block the write — the body has to).

> **Critical lesson (DECISIONS.md):** `rw_edit_message` runs as the function owner (`postgres` in dev, `rw_migrator` in prod). The function owner has `BYPASSRLS` *if it is also `SUPERUSER`*. In this project `rw_migrator` is a plain LOGIN role (no `BYPASSRLS`), so RLS *does* fire inside the procedure body — BUT the `rw_message_update` policy's `USING (rw_author_id = GUC)` clause isn't sufficient on its own when the body does multiple statements. The procedure must re-enforce the author gate explicitly. Migration 0040's `rw_edit_message` body does this; do not remove it when refactoring.

The full list of SECURITY DEFINER wrappers lives in [`/db/migrations/0100` `0110` `0120` `0140`](../../db/migrations/). The pattern is always: function owner is `rw_migrator`; body checks GUC actor + membership explicitly because RLS is bypassed from inside.

## Step 6: Triggers — keeping `rw_embedding` in lockstep

**See the shipped file:** [`/db/migrations/0050_triggers.sql`](../../db/migrations/0050_triggers.sql) — one-line summary: `rw_compute_message_embedding()` AFTER INSERT OR UPDATE OF `rw_body` on `rw_message`; `RAISE WARNING` if a row landed without an embedding (the seed script can't silently skip the embed step). Idempotent — `DROP TRIGGER IF EXISTS` then `CREATE TRIGGER`.

The project chose to compute embeddings in the application (`infrastructure/ai/MistralAdapter`) and pass them in via the `rw_send_message` parameter list. The trigger exists as a guardrail — `RAISE WARNING` if a row landed without one, so the seed script can't silently skip the embed step.

> **Known follow-up (issue #24, not yet shipped):** the trigger is currently a no-op stub in production deployments — it `RAISE WARNING`s but does not actually call Mistral from inside the database (no HTTP from PG). The seed script is the only place embeddings are populated today. Filing a follow-up that re-introduces the application-side `rw_message.embedding` population in the seed is the cleanest path forward; avoid trying to call Mistral from inside the trigger body.

## Step 7: Views

**See the shipped file:** [`/db/migrations/0070_views.sql`](../../db/migrations/0070_views.sql) — one-line summary: one view, `rw_visible_message`, declared **WITH (security_invoker = true)** so RLS on `rw_message` applies through it (PG 15+ changed the default to `security_invoker = false`, which would let the view run as the migrator / superuser and silently re-introduce the leak the policies are here to close). Filter is `rw_deleted_at IS NULL`.

RLS still applies on top of the view (Postgres honours RLS on the underlying table). This view just collapses the "is it logically deleted?" check so query authors don't have to remember.

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

## Step 9.5: Idempotent send + the `was_replay` OUT parameter

The Phase 1 `rw_send_message(...)` function uses `ON CONFLICT DO NOTHING + RETURNING` for idempotency on `(rw_author_id, rw_client_ref) WHERE rw_client_ref IS NOT NULL`. Phase 4 added an `out_was_replay boolean` OUT parameter so the application can distinguish a fresh insert (201) from an idempotent replay (200, with `X-Idempotent-Replay: true`).

**Why this matters:** a naive "compare timestamps" heuristic is unreliable (rows are inserted in the same transaction, so `now() - rw_created_at` is microseconds even for a replay). The cleanest signal is a flag from the function itself.

**Two SQL gotchas to know about** (caught and fixed in Phase 4):

1. **OUT parameter name = column name → ambiguous reference.** When the function has an OUT parameter called `rw_author_id` and a column called `rw_author_id`, `WHERE rw_author_id = p_author_id` in the body is ambiguous. Workarounds: (a) prefix the OUT parameters (`out_was_replay`, `out_rw_id`, …) — chosen here; (b) qualify every column reference with the table alias. (a) is shorter.
2. **Partial unique index on `(rw_author_id, rw_client_ref) WHERE rw_client_ref IS NOT NULL`.** Postgres' `ON CONFLICT (rw_author_id, rw_client_ref)` doesn't accept a `WHERE` clause directly when a partial index is in play. The conflict target here is the named constraint (`uq_rw_message_client_ref`) plus the partial predicate, so we use:

```sql
INSERT INTO rw_message (rw_channel_id, rw_author_id, rw_client_ref, rw_body)
VALUES (...)
ON CONFLICT (rw_author_id, rw_client_ref)
    WHERE rw_client_ref IS NOT NULL
    DO NOTHING
RETURNING * INTO v_msg;
```

See [`/backend/db/migrations/0110_rw_send_message_replay_flag.sql`](../../db/migrations/0110_rw_send_message_replay_flag.sql).

## Step 9.7: ts_headline + per-channel unread + bulk mark-read

Three SECURITY DEFINER functions added in Phase 5 (`db/migrations/0120_rw_search_messages.sql`):

- `rw_search_messages(p_channel_id, p_query, p_limit, p_actor_id)` → `TABLE` with `ts_headline` highlight (`<mark>…</mark>` around matches). Locale is pulled from `rw_user.rw_locale` (NOT from a parameter the client can lie about, NOT hardcoded). See [`db/migrations/0120_rw_search_messages.sql`](../../db/migrations/0120_rw_search_messages.sql) — one-line summary: 3 SECURITY DEFINER functions, all with GUC-actor + channel-membership defense-in-depth checks because SECURITY DEFINER bypasses RLS.
- `rw_unread_count_for_channel(p_channel_id, p_user_id)` → `integer` — counts visible messages NOT in `rw_message_read` for that user. Returns `0` for non-members (defense in depth, since SECURITY DEFINER bypasses RLS).
- `rw_mark_channel_read(p_channel_id, p_user_id)` → `integer` — single `INSERT … SELECT … WHERE NOT EXISTS … ON CONFLICT DO NOTHING RETURNING` statement. Idempotent (the UNIQUE constraint on `(rw_message_id, rw_user_id)` swallows duplicates). Returns the count of newly-inserted rows.

**Three gotchas to know about** (caught + fixed while building Phase 5):

1. **`char(2)` does not resolve the `(regconfig, text)` overload of `plainto_tsquery` / `to_tsvector` / `ts_headline`.** `rw_user.rw_locale` is `char(2)` in the schema (values `'es'`/`'en'`), but the function signature is `(regconfig, text)`. Naive `plainto_tsquery(rw_locale, ...)` raises `function plainto_tsquery(character, text) does not exist`. Fix: expand `'es'`/`'en'` to `'spanish'`/`'english'` (with a `'simple'` fallback) in the function body and cast `v_locale::regconfig` at every FTS call site.
2. **SECURITY DEFINER bypasses RLS — explicit membership check is mandatory.** Without `IF NOT EXISTS (SELECT 1 FROM rw_channel_member WHERE ...)` at the top of each function, a non-member could call `rw_search_messages(their_channel_id=...with_other_users_only)` and bypass RLS entirely. The GUC actor check (`p_actor_id = current_setting('app.current_user_id', true)::uuid`) is also required.
3. **The migration is `DROP FUNCTION IF EXISTS` + `CREATE FUNCTION` (NOT `CREATE OR REPLACE FUNCTION`).** Because the function signatures changed (e.g. Phase 4 added the `out_was_replay` OUT parameter), `CREATE OR REPLACE` refuses if the OUT parameter list differs. `DROP` then `CREATE` is the only safe path. Same pattern applies if a future phase adds new functions.

See the `Search messages + per-channel unread + bulk mark-read` use case in [`/backend/app/messages.py`](../../backend/app/messages.py) — one-line summary: `SearchMessages` validates input (query 1..200, limit 1..50) and projects hits with the actor's `is_mine` flag; `MarkChannelRead` is a thin dispatcher around `repo.mark_channel_read`.

## Step 10: Seed / Bronze–Silver (ARCHITECTURE §9)

Even for a one-shot load, Bronze keeps the seed as received (auditable, immutable). Silver is the 3FN model.

**See the shipped file:** [`/backend/scripts/seed.py`](../../backend/scripts/seed.py) — one-line summary: Bronze loads `seed.json` into `stg_seed_message (rw_payload jsonb)` (migration 0090); Silver does the 1FN→3FN load into `rw_user / rw_channel / rw_channel_member / rw_message` with parameterized inserts. Embeddings are computed in Python (`infrastructure/ai/MistralAdapter`) and inserted in batches of up to 512 texts per `embeddings.create(...)` call — the Mistral free tier's batch limit.

## Step 11: Testing — BDD against real PostgreSQL

Use **`testcontainers-python`** so tests exercise the *real* `pgvector` extension and the *real* RLS policy. Mocking RLS defeats the point.

**See the shipped fixture:** [`/backend/tests/conftest.py`](../../backend/tests/conftest.py) — one-line summary: `pg_container` (session-scoped `PostgresContainer("pgvector/pgvector:pg18")`), `_bootstrap` (applies migrations + creates `rw_app_login` with the test password, *skipping* `0002_roles.sql` so the testcontainer's role names don't collide), `pg_super_url` / `pg_app_url` (the two URLs the rest of the suite uses), `super_conn` (superuser — setup only) and `actor_conn` (`rw_app_login` — every read-and-assert goes here). Per-scenario TRUNCATE (autouse `_seed`) keeps tests independent; the canonical Valentina / Camila UUIDs are exported from `conftest` so step defs and the feature file stay in sync.

The two mandatory BDD scenarios from `ARCHITECTURE §10` live in [`/backend/tests/features/membership.feature`](../../backend/tests/features/membership.feature). They MUST pass against the real `pgvector/pgvector:pg18` image and the real `rw_app_login` role (no `BYPASSRLS`).

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
| Direct `INSERT` / `UPDATE` / `DELETE` on `rw_refresh_token` from the application | The runtime role has no table privileges on `rw_refresh_token` (REVOKEd in migration 0140); use the SECURITY DEFINER functions (`rw_insert_refresh_token`, `rw_revoke_refresh_token`, etc.) |
| Direct `INSERT` on `rw_copilot_usage` from the application | The runtime role has only `SELECT` on `rw_copilot_usage` (migration 0140); audit writes go through `rw_record_copilot_usage(...)` SECURITY DEFINER |

## Step 13: Where to go next

- For the Python-side RLS enforcement (psycopg transaction, GUC setting, middleware), use the `fastapi-development` skill.
- For embedding / chat model wiring behind the `EmbeddingProvider` / `ChatProvider` ports, use the `ai-provider-integration` skill.
- For the BDD feature files and pytest-bdd step definitions, use the `pytest-bdd-testcontainers` skill.
- For architectural questions, the source of truth is `/docs/ARCHITECTURE.md`. If this skill and the architecture disagree, **the architecture wins**.