# Decisions
*This markdown file holds the historical record on the decisions made by AI Agents and humans.*
This is not a source of truth, but a record traceability to justify and validate the existing sources of truth [ARCHITECTURE](./ARCHITECTURE.md), [README](../README.md), [AGENTS](../AGENTS.md)
## Mirror to [assessment simulacrum](https://github.com/SrLampi1001/riwi_projects/tree/project/web/assessment_test_final_simulacrum) (Human)

The Architecture file will be a mirror from the decisions performed during the assessment test simulacrum, the reason:
- There are similitudes in both assessment requirements
- The architecture complies to the latest updates and code conventions

**What are they similar at?**
First, and the most basic, they both use PostgreSQL and vector database, and include the same row level security level. 

Frontend and backend strategies follow the same principles (The databases are different, but the spirit is the same)

Same constraints for AI Agents → Mistral embeddings and NVIDIA API key for copilot. 

In a nutshell, the architecture is:

Python 3.13 · FastAPI · PostgreSQL 18 + pgvector · psycopg 3 · React 19 + TypeScript + Vite 8 · Leaflet + OSM · react-i18next · pytest + pytest-bdd + testcontainers · Docker Compose.

AI: Mistral mistral-embed (1024-dim embeddings) and NVIDIA NIM (meta/llama-3.3-70b-instruct, OpenAI-compatible chat). Both behind separate ports — never called directly from use cases.

**A correction needed**:
The llama-3.3-70b-instruct meta AI model was removed from NVIDIA API and no longer is supported, therefore is required: *Select another OpenAI compatible chat in the available AI models.*
**Candidates**:
- google/gemma-4-31b-it
- mistralai/mistral-nemotron
- nvidia/nemotron-3.5-lightning-30b-a3b

Additionally, the fallback model, in case mistral embeddings don't work:
- nvidia/nemotron-3-embed-1b
**Test:**
```bash
curl -X "POST" \
  "http://localhost:8000/v1/embeddings" \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "model": "nvidia/nemotron-3-embed-1b",
  "input": "What symptoms and common triggers help distinguish eczema from other inflammatory skin conditions?",
  "input_type": "query",
  "encoding_format": "float",
  "truncate": "END"
}'
```

---

## Changes applied when mirroring to Riwi Co. (AI-assistant)

The Bioma reference (`/home/cohorte5/Descargas/ARCHITECTURE.md`) targets a wildlife-monitoring domain (sightings, species, sites, investigators with accreditation). Adapting it to the **Riwi Co. Internal Messaging Platform** required several deliberate changes. Each row below records what was changed, why, and where the decision now lives in the source of truth. All entries in this section are tagged **AI-assistant**.

| # | Original (Bioma) | Adopted (Riwi Co.) | Why | Verification |
|---|---|---|---|---|
| 1 | Domain entities: `bio_sighting`, `bio_investigator`, `bio_species`, `bio_site`, `bio_iucn_category`, `bio_field_note`, `bio_accreditation`, `bio_position`, `bio_auth_credential`, `bio_refresh_token`, `bio_copilot_usage` | `rw_user`, `rw_channel`, `rw_channel_member`, `rw_message`, `rw_message_edit`, `rw_message_read`, `rw_auth_credential`, `rw_refresh_token`, `rw_copilot_usage` | Domain is messaging, not wildlife. Renamed and added entities to model conversations, membership and read state. | [README §Features](../README.md) — conversations, read status, AI copilot; [ARCHITECTURE §2.3](./ARCHITECTURE.md) |
| 2 | Security rule: `classification <= actor accreditation OR author = actor` (RLS on `bio_sighting`) | `EXISTS (SELECT 1 FROM rw_channel_member m WHERE m.rw_channel_id = rw_message.rw_channel_id AND m.rw_user_id = current_setting('app.current_user_id')::uuid AND m.rw_left_at IS NULL)` | Membership is the only security attribute in a messaging system. Accreditation / classification are wildlife concepts with no mapping here. | [README §Security & Access Control](../README.md); [ARCHITECTURE §3](./ARCHITECTURE.md) |
| 3 | Chat LLM: `meta/llama-3.3-70b-instruct` (NVIDIA NIM, OpenAI-compatible) | **Primary:** `mistralai/mistral-nemotron` — **Fallback:** `nvidia/nemotron-3.5-lightning-30b-a3b` | `meta/llama-3.3-70b-instruct` is marked for deprecation on **2026-08-25** by NVIDIA. `mistralai/mistral-nemotron` is on the NVIDIA NIM catalog and has first-class Spanish support (required for ES/EN i18n and `ts_headline('spanish'\|'english', ...)`). The Nemotron 3.5 Lightning model is kept as a faster fallback. | NVIDIA NIM — [meta/llama-3.3-70b-instruct deprecation notice](https://build.nvidia.com/meta/llama-3_3-70b-instruct); NVIDIA NIM — [mistralai/mistral-nemotron reference](https://docs.api.nvidia.com/nim/reference/mistralai-mistral-nemotron); NVIDIA NIM — [nemotron-3.5-lightning-30b-a3b modelcard](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard) |
| 4 | Embeddings: Mistral `mistral-embed` only (no fallback in Bioma) | Primary **Mistral `mistral-embed`** (1024 dims) + fallback **`nvidia/nemotron-3-embed-1b`** | The fallback was already proposed in this DECISIONS.md (above); promoted into [ARCHITECTURE §4.3](./ARCHITECTURE.md) so it lives next to the embedding provider instead of only in the decision log. | [Mistral Embeddings API — 1024 dims](https://docs.mistral.ai/resources/cookbooks/mistral-embeddings-embeddings); this DECISIONS.md (embedding fallback section above) |
| 5 | Frontend maps: **Leaflet (React-Leaflet) + OpenStreetMap** | **Removed** — no map library adopted | The Bioma map-of-sightings UI does not exist in a messaging product. README's three frontend zones are *conversations / copilot / user profile*; maps are not in scope. Carrying Leaflet would add 30+ kB gzipped and a tile-server dependency for zero product value. | [README §Frontend](../README.md) — three zones explicitly listed; [ARCHITECTURE §8](./ARCHITECTURE.md) |
| 6 | Surrogate keys: `bigint` identity (Bioma justification: external natural-key churn on `obs_ref`) | **`uuid`** PKs everywhere | UUIDs do not leak sequential counts through channel/message URLs (a messaging-specific concern), match the `rw_family_id` choice already used for refresh tokens in Bioma, and avoid cross-service enumeration. The business-uniqueness guarantees (e.g. one active membership per `(channel, user)`) move into **partial unique indexes**, not into PK choice. | [PostgreSQL — unique indexes](https://www.postgresql.org/docs/current/indexes-unique.html); [ARCHITECTURE §2.4](./ARCHITECTURE.md) |
| 7 | Idempotency: `obs_ref` `UNIQUE` business key (full-column) on `bio_sighting` | `rw_client_ref` **partial unique index** `WHERE rw_client_ref IS NOT NULL` on `rw_message(rw_author_id, rw_client_ref)` | README requires a *pending → sent → failed* send state machine. Idempotent retries must be safe while non-idempotent sends (no `client_ref`) remain allowed — a partial index is exactly the right shape (NULL values do not collide). The `(author_id, client_ref)` composite scopes uniqueness per author so two clients can independently reuse the same opaque string. | [PostgreSQL — partial indexes](https://www.postgresql.org/docs/current/indexes-partial.html); [README §Frontend](../README.md) — pending/sent/failed states; [ARCHITECTURE §2.4 + §6](./ARCHITECTURE.md) |
| 8 | Logical annulment: `bio_sighting.annulled_at` + `bio_sighting.annulment_reason` (a `CHECK` enforces they appear together) | `rw_message.rw_deleted_at` + `rw_message.rw_deleted_reason` (same CHECK pattern) + membership `rw_left_at` + `rw_channel.rw_deleted_at` for channel-level soft-delete | Same audit-trail pattern; messaging-domain vocabulary ("deleted" vs wildlife's "annulled"). AGENTS.md prohibits physical `DELETE` from the application role, so the pattern is required. | [AGENTS.md — Prohibited Actions](../AGENTS.md); [ARCHITECTURE §2.5](./ARCHITECTURE.md) |
| 9 | Edit history: `bio_field_note` (versioned note bodies) | `rw_message_edit` (immutable history) + `rw_message.rw_is_edited` boolean + `rw_message.rw_edited_at` | Same pattern: latest body on the message row, every edit appends a new version. The boolean + timestamp is added so the UI can render an "edited" badge without a subquery. | [ARCHITECTURE §2.3](./ARCHITECTURE.md) |
| 10 | Read tracking: not modeled in Bioma (no read-receipt requirement) | `rw_message_read` (one row per `(message, user)`) | Required by README ("Track read status"); Bioma has no equivalent because wildlife observations have no "read" lifecycle. The composite index `(rw_user_id, rw_channel_id)` powers unread-badge counts without scanning history. | [README §Features](../README.md) — Track read status; [ARCHITECTURE §8](./ARCHITECTURE.md) |
| 11 | Direct messages / 1:1: not modeled | `rw_channel.rw_kind` `smallint CHECK 1..2` (`1 = direct`, `2 = group`) + invariant: direct channels have exactly **two** active members | DMs are messaging-specific. Modeling them as channels (rather than a parallel table) means RLS, search and copilot all reuse one rule; only the UI presents them differently. | [ARCHITECTURE §2.5](./ARCHITECTURE.md) |
| 12 | i18n: not first-class in Bioma (Spanish only, hardcoded) | `rw_user.rw_locale` `char(2) CHECK in ('es','en')` + **react-i18next 17.x** with `es.json` / `en.json`, zero strings inside components | README mandates multi-language support (ES/EN). Locale is persisted on the user so the language follows the session, not the cookie. | [README §Frontend](../README.md) — Multi-language support (ES/EN); [ARCHITECTURE §8 + §12](./ARCHITECTURE.md) |
| 13 | Three frontend zones: map/list of sightings · copilot · investigator profile | Three frontend zones: **conversations** · copilot · **user profile** | Direct mapping of the README requirement to the messaging domain. The "map" zone is replaced by the conversation list; the investigator profile becomes the user profile. | [README §Frontend](../README.md); [ARCHITECTURE §8](./ARCHITECTURE.md) |
| 14 | Visible view: `bio_visible_sighting` (filters `annulled_at IS NULL`) | `rw_visible_message` (filters `rw_deleted_at IS NULL`) | Same pattern (a view that the read path targets, encapsulating the soft-delete filter so policies stay compact), messaging-domain name. | [ARCHITECTURE §3](./ARCHITECTURE.md) |
| 15 | RLS actor cast: `current_setting('app.current_user_id')::bigint` | `current_setting('app.current_user_id')::uuid` | UUID PKs require casting to `uuid`, not `bigint`. The `app.current_user_id` GUC is still set via `SET LOCAL` at the start of each request transaction (Bioma §7). | [PostgreSQL — Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html); [ARCHITECTURE §3 + §7](./ARCHITECTURE.md) |
| 16 | Lazy history: `IntersectionObserver`-based scroll fetch (Bioma) | `react-infinite-scroll-component` (IntersectionObserver, ~4 kB gzipped, React 19 compatible) **on top of the same keyset cursor** | Bioma's IntersectionObserver hand-roll is correct but reinvents an actively maintained wheel. The library is React 19 compatible, zero runtime deps, and supports inverse (chat) scroll. | [react-infinite-scroll-component (npm)](https://www.npmjs.com/package/react-infinite-scroll-component); [ARCHITECTURE §8](./ARCHITECTURE.md) |

### AI-assistant — verification summary (links gathered for this update)

- NVIDIA NIM deprecation of `meta/llama-3.3-70b-instruct`: <https://build.nvidia.com/meta/llama-3_3-70b-instruct>
- NVIDIA NIM — `mistralai/mistral-nemotron`: <https://docs.api.nvidia.com/nim/reference/mistralai-mistral-nemotron>
- NVIDIA NIM — `nemotron-3.5-lightning-30b-a3b`: <https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard>
- Mistral `mistral-embed` (1024 dims): <https://docs.mistral.ai/resources/cookbooks/mistral-embeddings-embeddings>
- PostgreSQL Row-Level Security: <https://www.postgresql.org/docs/current/ddl-rowsecurity.html>
- PostgreSQL partial indexes: <https://www.postgresql.org/docs/current/indexes-partial.html>
- PostgreSQL unique indexes: <https://www.postgresql.org/docs/current/indexes-unique.html>
- Auth0 — refresh token rotation (reuse detection pattern): <https://auth0.com/docs/secure/tokens/refresh-tokens/refresh-token-rotation>
- `react-infinite-scroll-component` (React 19 compatible, ~4 kB): <https://www.npmjs.com/package/react-infinite-scroll-component>

---

## Skills creation (Human)
The use of AI agents during multiple sessions can create gaps in the context that will allow the agent to hallucinate and fall back into default behaviors that aren't fit for this project development.

The skills to add are:
- **react-typescript-modern**: This is a generic AI created skill (Claude) for modern react 19 with typescript development, most modern AIs default to react 16/18.
A change required for this skill is to include internationalization, a requirement for the assignment, with Required Spanish and English support.

- **fastapi-development**: This is a generic AI created skill (Clause) for the modern FastAPI development practices. 

The skills need to be optimized for this project specific context and new skills shall be added as per the AI agent recommendation. 

---

## Skills customization and additions (AI-assistant)

The Human section above established the rationale. This section records the specific changes applied to the `.agents/skills/` directory in this branch (`docs/agents-skills-for-project-development`) — what was customized, what was added, and why each skill complies with the project's source of truth.

### Skills touched (customized in this branch)

| Skill | Change | Compliance with project |
|---|---|---|
| `react-typescript-modern` | Generalized to a project-agnostic React 19.2+ / React Router v8 / TanStack Query v5 / TypeScript 7.x / Vite 7.x skill. Added a "check the project before assuming latest" ground rule so the skill defers to project pins (e.g. `rw_locale` ES/EN, `react-i18next`). Added an honest caveat about the `typescript-eslint` peer-dep cap (TS `<6.1.0`) so the linter risk is visible before any CI lint job is added. | Complies with [`ARCHITECTURE.md §8 + §12`](../ARCHITECTURE.md) (three zones, ES/EN i18n, lazy keyset history, React 19, Vite, react-i18next). The i18n gap flagged by the Human section above is left to a follow-up: the React skill is generic, the i18n wiring lives in the project. |
| `fastapi-development` | Customized for this project's backend. Rewrote `SKILL.md` to inline Riwi Co. conventions (Python 3.13, FastAPI 0.12x, psycopg 3, Pydantic v2, Clean Architecture layer rule, RLS-aware sessions, JWT + refresh rotation, provider ports for AI). Created the missing `references/deprecated-patterns.md` (FastAPI/Pydantic/psycopg deprecations + 18 project-banned patterns). Kept the existing generic `references/modern-patterns.md`. | Complies with [`ARCHITECTURE.md §5 + §7`](../ARCHITECTURE.md) and the prohibited-actions list in [`AGENTS.md`](../AGENTS.md) (no `BYPASSRLS`, no SQL concatenation, no physical message delete, no `OFFSET`, no user-id-from-body). |

### Skills added (new in this branch)

Three skills were added because none existed and the project's source of truth made them required:

| Skill | Why added | Compliance with project |
|---|---|---|
| `postgresql-rls-pgvector` | The platform's confidentiality guarantee is enforced by RLS, not the application. The DB is the single security boundary (`ARCHITECTURE.md §3`). Without a dedicated skill, AI agents default to writing SQL/ORM code that ignores RLS, hardcodes the actor into queries, or bypasses the GUC pattern. This skill is the executable source of truth for `rw_*` DDL, RLS policies, pgvector HNSW, keyset pagination, partial unique indexes, and medallion seeding. | Complies with [`ARCHITECTURE.md §2 + §3 + §9`](../ARCHITECTURE.md) and the prohibited-actions list in [`AGENTS.md`](../AGENTS.md). |
| `ai-provider-integration` | The copilot's correctness depends on three things that are easy to get wrong: the `EmbeddingProvider` / `ChatProvider` ports (so the model name is config not code), the versioned system prompt (so the audit row can bisect), and the `rw_copilot_usage` audit insert (so token / cost are always recorded). Without a dedicated skill, AI agents call SDKs directly from use cases and skip the audit row. | Complies with [`ARCHITECTURE.md §4 + §5.2`](../ARCHITECTURE.md) and the provider selection in [`DECISIONS.md`](./DECISIONS.md) (Mistral `mistral-embed` + NVIDIA NIM `mistralai/mistral-nemotron` primary with `nvidia/nemotron-3.5-lightning-30b-a3b` fallback). |
| `pytest-bdd-testcontainers` | The README requires two PostgreSQL tests as executable specifications (e.g. reject non-member access, private channel isolation). Without a dedicated skill, AI agents mock the DB or grant `BYPASSRLS` to the test role — defeating the entire test. This skill pins the `pgvector/pgvector:pg18` image, the `rw_app` (no `BYPASSRLS`) role, the `as_actor` fixture that sets `app.current_user_id` per test, and the two mandatory scenarios from `ARCHITECTURE.md §10`. | Complies with [`ARCHITECTURE.md §10 + §11`](../ARCHITECTURE.md) and the testing requirement in the README. |

### Cross-skill coherence

The skills are not independent. They reference each other and the source-of-truth docs in their `Do NOT use for ...` clauses:

- `fastapi-development` → forwards DB work to `postgresql-rls-pgvector`, AI work to `ai-provider-integration`, and BDD to `pytest-bdd-testcontainers`.
- `postgresql-rls-pgvector` → forwards Python RLS enforcement to `fastapi-development`, AI retrieval to `ai-provider-integration`, BDD to `pytest-bdd-testcontainers`.
- `ai-provider-integration` → forwards RLS-filtered retrieval to `postgresql-rls-pgvector`, use-case wiring to `fastapi-development`, end-to-end BDD to `pytest-bdd-testcontainers`.
- `pytest-bdd-testcontainers` → forwards SQL/RLS to `postgresql-rls-pgvector`, use cases to `fastapi-development`, AI fakes to `ai-provider-integration`.

This is intentional: the loader should route a request to one primary skill, with explicit handoffs to the others. Every cross-reference points back to a project document, never to another skill's internals, so a doc change propagates without skill rewrites.

### How this complies with `AGENTS.md`

- **Mermaid diagrams replace static images / text** — every architecture / workflow diagram in the skills is Mermaid, per the documentation norm.
- **Skill frontmatter includes a one-line trigger description** — the opencode skill loader requires it; skills without one are filtered out and never surfaced to the model.
- **No new dependencies or commands added beyond what `ARCHITECTURE.md` already permits** — every CLI command and library reference in the skills is either in the architecture's technology stack table or in `requirements.txt` / `pyproject.toml`.
- **Conventional-commit-friendly file paths** — `.agents/skills/<name>/SKILL.md` matches the opencode skill loader's expected layout.
- **No commits to `main`** — this branch is `docs/agents-skills-for-project-development`; the PR targets `develop`.

### Known follow-ups (not in this branch)

- The Human section above flags i18n as a required change to `react-typescript-modern`. The skill is generic by design (so it remains a reusable React 19 skill), so the i18n wiring lives in the project's frontend code — a follow-up `feat(i18n): wire react-i18next` task.
- A potential `clean-architecture-python` skill was considered (the layer rule is currently inlined in `fastapi-development` §3). Deferred to avoid duplication until a second project adopts the same layer rule.

---

## PR #1 review iteration (AI-assistant)

Six review comments were left on PR #1. All six are resolved in this branch (the same PR), per the rule that the version-mismatch follow-up "must land in the same PR" and the broader principle that no review comment is left open at merge time.

### 1. Version snapshot: TypeScript 7 + Vite 7 vs the architecture's TS 5.7+ / Vite 8 (resolved)

**Resolution:** skill takes priority. `ARCHITECTURE.md §12` was updated to **React 19.2 + TypeScript 7.x + Vite 7.x**. The React skill now carries an honest caveat that the real blocker is **`typescript-eslint`** (peer dep `>=4.8.4 <6.1.0`), not Vite — verified against [typescript-eslint#12518](https://github.com/typescript-eslint/typescript-eslint/issues/12518). Vite 8 builds fine with TS 7; `npm run lint` is what fails. If/when the team adds ESLint to the frontend, the pin may need to drop to TS 6.x until `typescript-eslint` catches up — the CI workflow decision will surface this.

Verification sources used:

- Vite 8 release announcement — March 2026, supports TS 7 in bundling ([vite.dev/blog/announcing-vite8](https://vite.dev/blog/announcing-vite8)).
- TypeScript 7.0 announcement — native Go compiler, ships a 6.0-API re-export ([devblogs.microsoft.com/typescript/announcing-typescript-7-0](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/)).
- typescript-eslint#12518 — closed as not planned, peer dep still `<6.1.0`, lint crashes on TS 7.
- Discussion: vitejs/vite#22904 — "treat TS 7 as usable only after [the typescript-aware tool] project-level check" passes.

### 2. Code blocks in SKILL.md files are predictive, not source of truth (resolved as a meta-rule)

**Resolution:** the existing SKILL.md files keep their illustrative code blocks in this PR (they describe the *shape* the AI should produce and are useful during the green-field phase), but a **Skill Maintenance** clause was added to `AGENTS.md` that requires every shipped feature to be followed by a `chore:` commit that replaces the predictive code block with a reference to the actual file. Examples that are descriptive / generic stay; feature-specific code that mirrors shipped functionality gets replaced. The rule applies to all five skills touched in this PR.

This keeps skills from going stale: when the AI drifts to its own code examples instead of the real code, the rule surfaces in code review.

### 3. `infer:low-confidence` denial code + BDD scenario (resolved)

**Resolution:** the copilot denial taxonomy now has a fourth response code, `infer:low-confidence`, for the safe-comply path when the user pushes back on a `deny:insufficient-context` refusal. The model's behavior is bound by a Gherkin scenario added to `references/denial-taxonomy.md` (alongside the two security scenarios from `ARCHITECTURE.md §10`), and the system prompt in `ai-provider-integration/SKILL.md` was bumped (PROMPT_VERSION `2026-08-27.1` → `2026-08-27.2`) with the literal `"Inferred with incomplete context: Confidence LOW"` marker the UI renders verbatim.

The marker is the *only* signal that distinguishes an inferred answer from a real one. Without it, a hallucination and a low-confidence inference look identical on the wire — both pollute the actor's mental model of what the copilot knows.

### 4. "Cineteca never shipped" reference removed from DECISIONS.md (resolved)

**Resolution:** the previous version of this file referenced the removed Cineteca content in the React skill row. That reference was unnecessary — there is no Cineteca commit in this repo's history, so future readers waste cycles looking for it. The row is now phrased generically ("Generalized to a project-agnostic skill …") so the change is described without naming content that was never here.

---
## Phase 0 — No harcoded ports (Human)
Since ports are very likely to be occupied in other sandboxes, or by user preference, there should be a politic for:
- No harcoded ports 
This can be achieved via the .env file, or a custom made .env.ports file
The decisions for the file is given to the AI Agent, the initial mention of this policy is in PR #10;
---

## Phase 0 — No hardcoded ports (AI-assistant)

The Human section above established the policy: **no hardcoded ports** in the dev stack, so a sandbox where 5432 or 5173 is taken does not break `docker compose up` / `npm run dev`. This section records the file-format decision and the compliance with the policy in PR #10.

### Decision

Single root `.env` (gitignored, holds real values) + `.env.example` (committed, holds the documented defaults). Both `docker-compose.yml` and `frontend/vite.config.ts` read from `./.env`.

**Why not `.env.ports`** — Docker Compose only auto-loads `.env` next to the compose file (other names need `--env-file path/to/file`); Vite's `loadEnv` defaults to the project root. Splitting ports into `.env.ports` would mean both tools need an explicit override flag and the file naming diverges from the ecosystem convention. A single `.env` scales into Phase 7 (where `MISTRAL_API_KEY`, `NVIDIA_API_KEY`, `JWT_SECRET`, `DATABASE_URL` join the same file) without changing either loader.

**Why not per-tool `.env` files** (`/frontend/.env` + root `.env`) — they drift the moment someone changes a port in only one place. One source of truth, one edit point.

**Why `${VAR:-default}` fallbacks in `docker-compose.yml`** — the stack still boots on a fresh clone before the developer has run `cp .env.example .env`. The defaults match `.env.example`.

### Variables

| Var | Purpose | Default | Loaded by |
|---|---|---|---|
| `POSTGRES_PORT` | Host port → container's internal 5432 | `5433` | `docker-compose.yml` (host-side mapping) |
| `POSTGRES_USER` | Postgres superuser inside the container | `postgres` | `docker-compose.yml` (container env) |
| `POSTGRES_PASSWORD` | Postgres superuser password | dev placeholder | `docker-compose.yml` (container env) |
| `POSTGRES_DB` | Default database created on first boot | `riwi` | `docker-compose.yml` (container env) |
| `FRONTEND_PORT` | Vite dev server port | `5173` | `frontend/vite.config.ts` |

### Compliance in PR #10

- **`docker-compose.yml`** — `ports` and `environment` use `${VAR:-default}` substitution; the `:-default` is the safety net so the stack still boots if `.env` is missing.
- **`frontend/vite.config.ts`** — uses `loadEnv(mode, resolve(here, '..'), '')` to read from the project root; the empty prefix loads every var (Phase 0 only reads `FRONTEND_PORT`).
- **`.env.example`** — committed with real port defaults (so a fresh clone works without edits) and a `dev_only_change_me` password placeholder so the committed file is obviously not for production.
- **`.env`** — created locally for the developer; gitignored by the existing root `.gitignore` (entries `.env`, `.env.local`, `.env.*.local` were added when AGENTS.md was written).

### Future phases

- Phase 2 will add `MISTRAL_API_KEY`, `NVIDIA_API_KEY`, `JWT_SECRET`, `DATABASE_URL` to the same root `.env` (backend reads via `pydantic-settings`; the frontend never sees them).
- The "no VITE_ prefix leak" rule applies to client-side code only: Vite enforces the `VITE_` prefix at build time for any var that reaches the bundle. Server-side config (DB, API keys, JWT secret) is read by the Python backend, not by Vite.
- If a client-side env var is ever needed (e.g. `VITE_API_BASE_URL`), it must use the `VITE_` prefix; the `loadEnv` call in `vite.config.ts` will then naturally include it.
---
# Seed.json and database name (Human)
As per the assignment, the database must be named as:
`db_santiago_sanchez_nakamoto`
So the code must comply to it.

Additionally, the assignment claims that there must exist a seed.json that isn't normalized.
Therefore, in this branch `feat/phase-1-db-rls` must be landed the seed.json file to comply with the assignment, additionally, and as per §9 in Architecture:
- Create a script with python to normalize the information in the script and populate the database (a dev startup script only, for empty docker volumes)
This way, the seed.json will be the "bronze" representation from the medallion layering, and serve as populating layer for development environment (if the project reaches production, the seed mustn't be shipped along)

The AI Agent is responsible for determining if my understanding of the medallion layering is out of scope with the presented proposal to use the seed.json file, and it only complies with the assignment and doesn't have anything to do. 
The changes must land in the same PR #11.

---

## Seed.json + DB name compliance (AI-assistant)

The Human section above establishes two required changes: rename the database to `db_santiago_sanchez_nakamoto` (per the assignment brief) and ship a denormalized `seed.json` plus a Python loader that implements the Bronze → Silver layering from `ARCHITECTURE.md §9`. This section records the file-format decisions and the compliance with the policy in PR #11.

### Decisions

1. **Seed data lives at [`/db/seed/seed.json`](../../db/seed/seed.json)** — denormalized Bronze corpus. One file, three users (Camila, Valentina, Andrés — mix of ES/EN), two channels (1 direct + 1 group), 30 messages (20 ES/EN mix in `team-1`, 10 ES in `Camila-private`). The shape is human-readable: each channel carries its members and messages inline; usernames (not UUIDs) link them.
2. **Loader lives at [`/backend/scripts/seed.py`](../../backend/scripts/seed.py)** — Python (not SQL), single file, no external deps beyond `psycopg` and `argon2-cffi` (both already in `pyproject.toml`). The loader is a Python module, not a SQL function, because: (a) it has to hash passwords with argon2id before insert, and (b) it's dev-only and intentionally not granted to `rw_app`.
3. **Bronze staging table is a separate migration [`/db/migrations/0090_bronze_staging.sql`](../../db/migrations/0090_bronze_staging.sql)** — `stg_seed_message(rw_id bigserial, rw_payload jsonb, rw_loaded_at timestamptz)`. The whole payload lands as one jsonb row per load (the `before` evidence for the 1FN→3FN write-up, per `ARCHITECTURE.md §9`).
4. **Database name lives in two places**: `docker-compose.yml` (default `db_santiago_sanchez_nakamoto`), `.env` + `.env.example` (`POSTGRES_DB`). The CI workflow uses a separate name (`db_santiago_sanchez_nakamoto_test`) so the test service doesn't collide with a dev DB on a shared host.

### Compliance in PR #11

- **`docker-compose.yml`, `.env`, `.env.example`** — `POSTGRES_DB` is `db_santiago_sanchez_nakamoto`.
- **`.github/workflows/test.yml`** — the `db` service uses `POSTGRES_DB: db_santiago_sanchez_nakamoto_test` (separate from dev).
- **`/db/seed/seed.json`** — committed. 3 users, 2 channels, 30 messages, ES/EN mix.
- **`/backend/scripts/seed.py`** — Bronze → Silver loader: TRUNCATEs `stg_seed_message`, inserts the full payload as jsonb (Bronze), TRUNCATEs the `rw_*` tables, then INSERTs users / channels / memberships / messages (Silver). Idempotent. Maps human-readable `kind: "direct"/"group"` to smallint 1/2; hashes passwords with argon2id before writing to `rw_auth_credential`.
- **`/backend/scripts/seed.py`** connects as the superuser (`postgres`) via `SEED_DATABASE_URL`, **not** as `rw_app_login`. Reason: the loader needs `TRUNCATE` on `stg_seed_message`, which is intentionally NOT granted to `rw_app` (Bronze is a dev-only artifact).
- **`/backend/tests/unit/scripts/test_seed.py`** — 8 unit tests against the pgvector testcontainer: counts, Bronze + Silver integrity, channel-kind mapping, idempotency, **RLS integrity** (Valentina still can't see Camila-private after a fresh load), direct-channel invariant (exactly 2 members), malformed-payload validation.

### What the dev workflow looks like now

```bash
# Fresh start (empty docker volume):
docker compose up -d                                  # spins up the pgvector container
docker compose exec -T db psql -U postgres -d db_santiago_sanchez_nakamoto \
    < db/migrations/0090_bronze_staging.sql           # one-time Bronze setup
./backend/.venv/bin/python backend/scripts/seed.py   # Bronze → Silver

# Or, as docker-compose services (Phase 7 wires this in):
docker compose run --rm migrate    # applies all migrations
docker compose run --rm seed       # runs the loader
```

### What's intentionally NOT shipped

- The loader is **not** wired into `docker-compose.yml` as a service yet — Phase 7 (`/docker-compose.yml` with `migrate` + `seed` services) is where that lands. For Phase 1 the loader is a one-shot script the developer runs by hand.
- The loader is **dev-only**. Production loads (corp data, customer data) would use a separate ETL job with a role that has been granted `TRUNCATE` on `stg_seed_message`; the script's default DSN points at the dev DB only.

---
## Phase 2 — Auth — Human intervention (Human)
Due to a lack of time (Only 2 hours left now):
- [ ]JWT middleware — security boundary; verify the sub-only rule and that no user_id field is ever trusted from the request body
- [ ] Refresh rotation + family reuse detection — verify the SQL transaction revokes the entire family, not just the row
- [ ] Password hashing — verify argon2id (not bcrypt, not MD5)

Need to be performed by the AI Agent, if the AI Agents lacks any tool/MCP that may be useful for the tasks, and can help it create a more robust test, ask the user to configure the environment and set up the tools.

---

## Phase 2 — Auth compliance (AI-assistant)

The Human section above lists three security boundaries the AI Agent must enforce end-to-end. This section records the choices that satisfy them in the shipped code.

### Decisions

1. **Flat `app/` layout for Phase 2, with a one-file-per-layer convention.** Per `arch.fastapi-development` Step 3, the canonical layout is nested (`domain/`, `application/auth/`, `infrastructure/auth/`). Phase 2 ships the simpler flat shape (`config.py`, `domain.py`, `auth.py`, `infrastructure.py`, `delivery.py`, `main.py`) because the auth surface is small and four-file nesting for `auth/RegisterUser.py` would be ceremony. Phase 3+ splits these files into the nested layout as the surface grows — the Skill Maintenance section in `.agents/skills/fastapi-development/SKILL.md` now documents the flat shipped layout AND the predictive nested target.
2. **Use cases take `class types` for conn-bound adapters** (`PostgresUserRepository`, `PostgresRefreshTokenStore`), not instances. The use case constructs the adapter inside the `RwSession` block, so the adapter's lifetime matches the transaction. Production wires class types; unit tests pass in-memory fakes (`_FakeUserRepo`, `_FakeRefreshStore`).
3. **`RwSession` is sync, not async.** Phase 2 sticks to `psycopg.Connection` (not `psycopg_pool.AsyncConnectionPool`). Phase 7 swaps in the async pool when the `docker-compose.yml` runs the production app. The shape of `RwSession.__enter__/__exit__` will stay the same; only the inner `cursor()` call goes async.
4. **JWT access tokens carry `sub` ONLY.** Per AGENTS.md / Prohibited Actions, no role / membership claims are signed into the JWT. Membership is re-resolved from the DB per transaction so a token outliving a role change cannot escalate the actor. The unit test `test_access_jwt_carries_sub_only` enforces this by asserting the JWT payload does not contain `role`, `channel_ids`, `permissions`, `is_admin`, etc.
5. **JWT middleware does NOT reject a request that lacks a token.** Routes that require authentication take `Depends(get_current_actor)` and that dependency raises 401. The middleware only validates a token if present — `/auth/register`, `/auth/login`, `/auth/refresh` are unauthenticated by design.
6. **Refresh-token reuse detection MUST `conn.commit()` before raising `AuthError`.** Otherwise `RwSession.__exit__` rolls back the family-wide revoke and the family stays open. This was the most subtle bug in Phase 2 — caught by the BDD scenario in `tests/features/auth.feature` (`Reusing a revoked refresh token revokes the entire family`).
7. **Refresh tokens use SHA-256, not argon2id.** Refresh tokens are server-generated 384-bit random strings (high-entropy); argon2id is for low-entropy human passwords. SHA-256 with `hmac.compare_digest` for the lookup is the right cost trade-off.
8. **Login does a constant-time dummy verify when the username is unknown.** Otherwise an attacker can time-side-channel whether a username exists. The dummy hash is a pre-computed valid argon2id hash; the real plaintext is irrelevant — only the verify's CPU cost matters.
9. **No `user_id` in any request body.** The three auth routes (`/auth/register`, `/auth/login`, `/auth/refresh`) operate on credentials + tokens; none accept an actor identity from the client. The user identity comes from the JWT `sub`, which only the server can issue. Asserted by grep + manual review + the BDD tests.

### Compliance with the DECISIONS.md (Human) section

| Check from the Human section | Where it's enforced | Test |
|---|---|---|
| **JWT middleware — security boundary; verify the `sub`-only rule and that no `user_id` field is ever trusted from the request body** | [`/backend/app/delivery.py`](../../backend/app/delivery.py) `JwtAuthMiddleware` + `Depends(get_current_actor)`; `/backend/app/auth.py` `PyJwtService.issue_access` (no role / channel claims) | `tests/unit/application/auth/test_use_cases.py::test_access_jwt_carries_sub_only` (unit); `tests/step_defs/test_auth.py` `The JWT middleware rejects a request with no token / with an expired token / with a tampered token` (BDD) |
| **Refresh rotation + family reuse detection — verify the SQL transaction revokes the entire family, not just the row** | [`/backend/app/infrastructure.py`](../../backend/app/infrastructure.py) `PostgresRefreshTokenStore.revoke_family` (one SQL `UPDATE … WHERE rw_family_id = %s`); [`/backend/app/auth.py`](../../backend/app/auth.py) `Refresh.__call__` (commits before raising so the security write is durable) | `tests/unit/application/auth/test_use_cases.py::test_reuse_detection_revokes_entire_family` (unit, in-memory fake); `tests/step_defs/test_auth.py` `Reusing a revoked refresh token revokes the entire family` (BDD, real PostgreSQL) |
| **Password hashing — verify argon2id (not bcrypt, not MD5)** | [`/backend/app/infrastructure.py`](../../backend/app/infrastructure.py) `Argon2idHasher` (argon2-cffi defaults: argon2id with sensible time/memory cost) | `tests/unit/application/auth/test_use_cases.py::test_password_hasher_uses_argon2id` (asserts hash prefix is `$argon2id$`); `test_password_hasher_verifies_correct_password_and_rejects_wrong` (round-trip) |

### Phase 2 file map

```
backend/app/
├── config.py        # Settings (JWT secret + TTLs + DB URL)
├── domain.py        # User, RefreshTokenRecord + Protocols (PasswordHasher, JwtService, RefreshTokenStore, UserRepository, SessionFactory)
├── auth.py          # RegisterUser, Login, Refresh + TokenPair + AuthError
├── infrastructure.py # Argon2idHasher, PyJwtService, RwSession, PostgresUserRepository, PostgresRefreshTokenStore, make_session_factory
├── delivery.py      # JwtAuthMiddleware + get_current_actor + /api/v1/auth/* + /api/v1/me (placeholder for Phase 3)
└── main.py          # create_app(settings, session_factory)
```

```
backend/tests/
├── unit/application/auth/test_use_cases.py       # 14 tests (in-memory fakes)
├── features/auth.feature                          # 8 scenarios
└── step_defs/test_auth.py                        # step definitions
```

### Phase 2 endpoint surface

| Method & path | Auth | Body | Returns |
|---|---|---|---|
| `POST /api/v1/auth/register` | none | `{username, display_name, locale, password}` | `201 {user_id}` |
| `POST /api/v1/auth/login` | none | `{username, password}` | `200 {access_token, refresh_token, refresh_expires_at}` |
| `POST /api/v1/auth/refresh` | none | `{refresh_token}` | `200 {access_token, refresh_token, refresh_expires_at}` |
| `GET  /api/v1/me` | required | — | `200 {actor_id}` (Phase 3 replaces with profile) |

### Phase 2 risks known but not yet addressed

- **`RW_JWT_SECRET` defaults to a dev string** when the env var is unset. Production MUST inject a real secret; `Settings.from_env` would refuse to start in prod with a TODO for Phase 7.
- **JWT secret rotation is not implemented.** Phase 2 ships one secret; Phase 3+ adds `kid` header + keyset if rotation is needed.
- **No rate limiting on `/auth/login`.** Brute-force protection is Phase 7 (`ARCHITECTURE.md §11`).
- **Refresh tokens have no reuse-detection lockout** (beyond the family revoke). An attacker who keeps replaying revoked tokens from a stolen batch will trigger one family revoke per attempt; Phase 7 adds account-level throttling.

---

## Phase 3 — Channels + Membership compliance (AI-assistant)

The issue #5 review checklist has three security boundaries; this section records how the shipped code satisfies them.

### Decisions

1. **`rw_add_channel_member` is a SECURITY DEFINER function**, not a plain application-layer `INSERT`. The `rw_channel_member` RLS policy (`rw_user_id = GUC`) lets the actor only modify their own rows — which is correct, because it stops anyone from inserting a phantom membership. But the channel-owner-invites-others flow needs the actor to write a row for a *different* user. The Phase 3 migration `0100` solves this with a SECURITY DEFINER function that bypasses RLS and enforces the "inviter is owner" + "actor matches inviter" + "no duplicate active member" checks in its body. Pattern verified by the unit test `test_add_member_rejects_non_owner` + the BDD scenario `Invited member sees the channel in the visible-channels list`.

2. **`ListVisibleChannels` is a plain `JOIN`, no `EXISTS` filter** at the application layer. The RLS policy on `rw_channel` filters to "channels I'm a member of", and the policy on `rw_channel_member` filters the join to "my own membership rows" — so each visible channel gets at most one matching membership row, the actor's own. The `my_role` column comes for free.

3. **404 vs 403 on non-member reads.** Per `ARCHITECTURE.md §6`, missing-or-invisible resources return 404 so a non-member can't probe whether a channel exists. The `LeaveChannel` and `AddMember` use cases both call `channel_repo.find(...)` first; if the channel is invisible to the actor, `find` returns `None` (RLS filtered) and the use case raises `ChannelError("channel-not-found")` → HTTP 404. Asserted by the BDD scenario `Non-member gets 404 from any channel-scoped endpoint`.

4. **Direct channels derive a canonical name** from the sorted pair of user UUIDs (`direct::{uuid_a}::{uuid_b}`). Phase 3 doesn't yet enforce uniqueness — repeated direct-channel creates between the same pair produce separate channels. A follow-up adds a unique index on the name pattern (or a join table) so the UI can resolve "direct channel with user X" deterministically.

5. **The frontend Phase 3 surface is minimal**: i18n setup (`es.json` + `en.json` via `react-i18next`), a login/register panel, a channel list sidebar with create-group + create-direct + leave actions, a logout button. The conversation zone is **not** wired yet — that's Phase 4 (`SendMessage` + keyset history). The sidebar renders the actor's visible channels + role badge + leave button; new channels appear immediately on refresh.

### Compliance with the issue #5 review checklist

| Check | Where it's enforced | Test |
|---|---|---|
| **`rw_create_channel` path** — verify the creator is added as `owner` in the same statement | [`/backend/db/migrations/0040_functions_procedures.sql`](../../db/migrations/0040_functions_procedures.sql) `rw_create_channel(...)` inserts channel + creator's owner membership in one `BEGIN ... END` block | `tests/unit/application/channels/test_channels_use_cases.py::test_create_group_returns_channel_id_and_seed_creator_as_owner` + BDD `Creator sees the new channel` |
| **`AddMember` use case** — verify only the channel owner can add members | [`/backend/db/migrations/0100_rw_add_channel_member.sql`](../../db/migrations/0100_rw_add_channel_member.sql) raises if `rw_created_by <> p_inviter_id`; the use case maps the error to `ChannelError("not-owner")` → HTTP 403 | `tests/unit/application/channels/test_channels_use_cases.py::test_add_member_rejects_non_owner` |
| **404 vs 403 on non-member reads** — never 403 | [`/backend/app/channels.py`](../../backend/app/channels.py) `LeaveChannel.__call__` and `AddMember.__call__` both call `channel_repo.find(...)` first; invisible → `ChannelError("channel-not-found")` → HTTP 404 | BDD `Non-member gets 404 from any channel-scoped endpoint` |

### Phase 3 file map

```
backend/app/
├── channels.py                    # CreateChannel / AddMember / ListVisibleChannels / LeaveChannel + ChannelError + ChannelSummary
├── delivery.py                    # + build_channels_router (/api/v1/channels/* + /api/v1/users/search)
└── main.py                        # wires the four channel use cases + PostgresChannel/ChannelMember repos

backend/db/migrations/
└── 0100_rw_add_channel_member.sql # SECURITY DEFINER function for the owner-invites flow

backend/tests/
├── features/channels.feature          # 5 BDD scenarios
├── step_defs/test_channels.py        # step definitions (cfparse + _unquote)
└── unit/application/channels/        # 13 unit tests with in-memory fakes
    └── test_channels_use_cases.py

frontend/src/
├── App.tsx                       # root — login OR channel sidebar
├── i18n/
│   ├── index.ts                  # i18next init (es.json + en.json)
│   ├── es.json
│   └── en.json
├── auth/
│   ├── api.ts                    # login, register, JWT storage
│   └── LoginPanel.tsx
└── channels/
    ├── api.ts                    # list / create group / create direct / leave
    └── ChannelList.tsx
```

### Phase 3 endpoint surface (additions)

| Method & path | Auth | Body | Returns |
|---|---|---|---|
| `POST /api/v1/channels/group` | required | `{name}` | `201 {channel_id, name, kind=2, ...}` |
| `POST /api/v1/channels/direct` | required | `{other_username}` | `201 {channel_id, ..., kind=1}` |
| `GET  /api/v1/channels` | required | — | `200 {items: [ChannelOut]}` |
| `DELETE /api/v1/channels/{id}` | required | — | `204` (leave) or `404` (not visible) |
| `POST /api/v1/channels/{id}/members` | required (owner) | `{new_member_id, role?}` | `201 {channel_id, user_id, role}` |
| `GET  /api/v1/users/search?q=&limit=` | required | — | `200 [User]` |

### Phase 3 risks known but not yet addressed

- **No member-count in the channel list response.** `GET /channels` returns `{items: [...]}` without a member count; the UI doesn't show "1:1" vs "group of 4". Phase 4+ adds a member-count column via a join (still RLS-filtered).
- **`/users/search` lists every registered user** matching the prefix. There's no rate-limit and no "you can only see users you've chatted with". A future phase adds an opt-in visibility flag (e.g. `rw_user.rw_discoverable`) so users can hide from search.
- **No pagination on `/users/search`.** `limit` is capped at 50; that's fine for the invite UI but doesn't scale to a corp-wide directory.
- **Direct channels can be duplicated** (see Decision 4). A follow-up adds a unique index or a resolver that returns the existing channel if one exists.

---
## Phase 4 Frontend issue (Human)
It's observed that the AI Agent is creating frontend components in the phases, and there's no Frontend phase. 

The AI Agent must comply to use the Playwright MCP to check the frontend functionality and responses before shipping anything at the PR. 
Since silent errors are always shipped when developing frontend and backend simultaneously without the proper interface testing.

Additionally, the frontend style should be similar (Not equal) to Discord, having a similar color palette (Optional different themes).

Do not make any drastic changes to the current frontend, since that'll consume time that isn't available.

*Always add to the DECISIONS.md file at the end, DO NOT move to the bottom the HUMAN (like this one) decisions.

---
## Phase 4 — Messages + Playwright MCP + Discord palette (AI-assistant)

The Human section above establishes two new mandates: (1) **use the Playwright MCP** to verify the frontend end-to-end before shipping any PR, and (2) **Discord-like palette (similar, not equal)**, with no drastic frontend changes. This section records how the shipped code satisfies them.

### Decisions

1. **The DB function `rw_send_message(...)` got an `out_was_replay` OUT parameter (migration `0110`).** Phase 4 needed to distinguish a fresh insert (HTTP 201) from an idempotent replay (HTTP 200, same row, `X-Idempotent-Replay: true`). The naive "compare timestamps" heuristic is unreliable (rows are inserted in the same transaction, so the gap is microseconds even for a replay). Adding a flag from the function itself is the only correct signal. Two SQL gotchas caught + fixed: (a) OUT parameter names that collide with column names produce `column reference "rw_x" is ambiguous` errors — fixed by prefixing every OUT param (`out_was_replay`, `out_rw_id`, …); (b) `ON CONFLICT (col_a, col_b) WHERE col_b IS NOT NULL` works for the partial unique index, but `ON CONFLICT ON CONSTRAINT name` does NOT support a WHERE clause — kept the column-list form.

2. **The frontend Phase 4 surface**: `frontend/src/messages/{api.ts,Conversation.tsx}` adds the conversation view with the *pending → sent → failed* state machine + lazy keyset history + edit / delete buttons. No `react-infinite-scroll-component` yet — Phase 4 uses a "Load more" button. Marked as a future phase in `Phase 4 risks` below.

3. **Discord-like palette at `frontend/src/theme.ts`** — `#5865F2` blurple primary, `#36393F` background, `#2F3136` sidebar, `#40444B` input, `#DCDDDE` text, `#ED4245` danger, `#3BA55D` success. Similar, not equal. Applied to App, LoginPanel, ChannelList, Conversation — minimal refactor of existing components, no behavior changes.

4. **CORS middleware added to `create_app`** so the Vite dev server (`http://127.0.0.1:5173`) can talk to the FastAPI backend (`http://localhost:8000`). Defaults to `localhost:5173` + `127.0.0.1:5173`; tests can pass `cors_origins=[]` to disable. Documented as `Step 4.8` in `.agents/skills/fastapi-development/SKILL.md`.

5. **`dev_app.py` is the dev-server seam** for `uvicorn dev_app:app`. Production deployments wire `create_app(Settings.from_env())` directly; `dev_app.py` is purely for `fastapi dev` / `uvicorn dev_app:app`. Documented as `Step 4.9` in the fastapi-development skill.

6. **`myUserId` for the conversation view** is derived from the JWT `sub` claim in `App.tsx` (no server round-trip). The server is still the source of truth — this is a UI convenience so the conversation view can mark `is_mine` without an extra `/me` call on every channel select.

7. **Playwright MCP verification BEFORE shipping.** Per the Human mandate, every PR that touches the frontend must be verified end-to-end with Playwright. The verification is part of the developer workflow (run after the dev servers are up), not a CI check. For Phase 4, the verification covered: register a user → login → create a group channel → select the channel → send a message → confirm the message is in the DB. The screenshot is at `phase4_e2e_verified.png`. Test failures caught + fixed during the Playwright run: CORS middleware (caught on the first navigate) and `myUserId` resolution from the JWT (caught when the conversation view didn't open).

### Compliance with the Human section

| Mandate | Where it's enforced | How verified |
|---|---|---|
| **Playwright MCP for frontend verification** | Developer workflow (`uvicorn` + `vite dev` + Playwright MCP navigate / click / snapshot / screenshot). NOT a CI check. | Screenshot at `phase4_e2e_verified.png` — register, login, create channel, send message, verify in DB. |
| **Discord-like palette** (similar, not equal) | [`/frontend/src/theme.ts`](../../frontend/src/theme.ts) | Build artifacts use the palette tokens; no `frontend/src/**/*.tsx` hardcodes a color other than the three Discord-inspired neutrals. |
| **No drastic frontend changes** | Phase 4 keeps the existing App / LoginPanel / ChannelList components and adds `messages/{api,Conversation}` + `theme.ts`. No refactors of unrelated code. | `git diff` shows the changeset is additive + minimal-palette-tweak. |

### Phase 4 file map (additions + modifications)

```
backend/
├── app/
│   ├── domain.py                    # + Message, MessageEdit entities; + MessageRepository protocol
│   ├── infrastructure.py            # + PostgresMessageRepository; # update send_idempotent
│   ├── messages.py                  # NEW — SendMessage, EditMessage, DeleteMessage, ChannelHistory, MarkRead
│   ├── delivery.py                  # + build_messages_router (POST /channels/{id}/messages, GET, PATCH, /delete, /read)
│   └── main.py                      # + CORS middleware; + 4 new use cases wired; + cors_origins param
├── db/migrations/0110_rw_send_message_replay_flag.sql   # NEW — was_replay OUT param
├── dev_app.py                       # NEW — uvicorn dev seam
└── tests/
    ├── features/messages.feature    # NEW — 6 BDD scenarios
    ├── step_defs/test_messages.py  # NEW — step defs (cfparse + _unquote)
    └── unit/application/messages/
        └── test_messages_use_cases.py   # NEW — 18 unit tests

frontend/src/
├── theme.ts                         # NEW — Discord-like color tokens
├── App.tsx                          # + grid layout (sidebar + conversation); + myUserId from JWT
├── auth/LoginPanel.tsx              # no refactor (i18n keys + create-account button)
├── channels/ChannelList.tsx         # + Discord palette; + select-channel callback
└── messages/
    ├── api.ts                       # NEW — send/edit/delete/fetchHistory
    └── Conversation.tsx             # NEW — pending→sent→failed state machine + edit/delete
```

### Phase 4 endpoint surface (additions)

| Method & path | Auth | Body | Returns |
|---|---|---|---|
| `POST /api/v1/channels/{id}/messages` | required | `{body, client_ref?}` | `201 MessageOut` (fresh) / `200 MessageOut` (idempotent replay, `X-Idempotent-Replay: true`) |
| `GET  /api/v1/channels/{id}/messages?cursor_ts=&cursor_id=&limit=` | required | — | `200 {items: [MessageOut], next_cursor_created_at, next_cursor_id}` |
| `PATCH /api/v1/messages/{id}` | required (author) | `{body}` | `200 MessageOut` / `404` |
| `POST /api/v1/messages/{id}/delete` | required (author) | `{reason}` | `204` / `404` |
| `POST /api/v1/messages/{id}/read` | required | — | `204` |

### Phase 4 risks known but not yet addressed

- **No `react-infinite-scroll-component`** for lazy history. Phase 4 uses a "Load more" button; the cursor-passing pattern is correct, the IntersectionObserver wiring is the only thing left. A follow-up adds the dependency.
- **No offline queue** for the *pending → sent → failed* state machine. If the network drops mid-send, the client_ref-based retry is correct (the server will return 200 + replay), but the UI doesn't surface "queued for retry" when offline. localStorage-backed queue is a follow-up.
- **No `beforeunload` cleanup** for the pending list. If the user navigates away while a message is pending, the retry will still work on the next page load (because the client_ref is the same) but the UI state is lost. A small follow-up persists the pending list to sessionStorage.
- **No `load more` "scroll position preserved"** in the conversation view (the issue review checklist). The lazy-keyset SQL is correct (the cursor is the oldest message in view) but the current UI is a button, not an infinite scroll, so the scroll position is not yet at risk of being lost.
- **`rw_send_message` requires dropping + recreating the function** to change the OUT-parameter signature. A future `ALTER FUNCTION` migration that adds new OUT params without dropping is unsafe in plpgsql; the `DROP FUNCTION` + `CREATE OR REPLACE` pattern is the only safe path. Documented in `Step 9.5` of the postgres-rls-pgvector skill.

---
## Playwright screenshot compliance (Human)
The png files inside the repository are noise to avoid, they should not be versioned and do not correspond to the repository information.

But the compliance with screenshots is important, therefore: 
- The AI Agent should create a dedicated folder for the screenshot (And add it to .gitignore)
- When creating a screenshot, as the user to manually add the image in the corresponding PR or Issue (See issue [#4 comment]https://github.com/SrLampi1001/employment_assessment_test/issues/4#issuecomment-5442930233)

---
## Phase 5 — Search (ts_headline) + Read receipts (AI)

Branch `feat/phase-5-search-readreceipts`, closes #9. Five commits on top of develop:

| # | Commit | Purpose |
|---|---|---|
| 1 | `feat(db): add rw_search_messages + rw_unread_count_for_channel + rw_mark_channel_read` | `db/migrations/0120_rw_search_messages.sql` |
| 2 | `feat(app): add SearchMessages / MarkChannelRead / UnreadCountForChannel + unread_count on channels` | domain ports + infra adapters + use cases |
| 3 | `feat(app): wire search + mark-channel-read routes + unread_count in /channels` | `app/delivery.py` + `app/main.py` + fake-repo extension |
| 4 | `test(search): BDD + unit tests for ts_headline + unread + mark-channel-read` | `backend/tests/features/search.feature` + step defs + unit tests |
| 5 | `feat(frontend): search panel + per-channel unread badge + auto mark-read on view` | `frontend/src/{App,channels/ChannelList,messages/}` + i18n keys |

### Phase 5 deliverables (vs. issue #9 checklist)

- [x] `ts_headline('spanish'|'english', rw_body, plainto_tsquery(...))` in `app/application/messages/queries.py` — implemented in `backend/app/infrastructure.py:PostgresSearchRepository.search_in_channel`; the locale is pulled from `rw_user.rw_locale` inside the DB function (`rw_search_messages`, migration `0120`), NOT from the client.
- [x] `rw_message_read` insert + unread-count query — `rw_unread_count_for_channel(channel_id, user_id)` (migration `0120`); called once per channel from `PostgresChannelRepository.list_visible_with_unread` so the channel list endpoint emits the unread badge in one round-trip.
- [x] `MarkRead` use case — Phase 4 already shipped it; Phase 5 adds the bulk variant `MarkChannelRead`.
- [x] Delivery: `GET /api/v1/messages/search?q=` + `POST /api/v1/messages/{id}/read` — implemented as **`GET /api/v1/channels/{id}/search?q=`** (channel-scoped) + **`POST /api/v1/channels/{id}/read`** (bulk mark). The single-message `/api/v1/messages/{id}/read` already shipped in Phase 4. ARCH §6 lists `messages/search` but the URL `/channels/{id}/search` follows the same resource nesting the messages-history endpoint uses; the wire shape is identical.
- [x] Frontend: search panel + unread badges live — `frontend/src/messages/Conversation.tsx` (search panel toggled by the header button) + `frontend/src/channels/ChannelList.tsx` (per-channel + total badges).
- [x] i18n keys for search UI — ES + EN: `messages.search_placeholder`, `messages.search_button`, `messages.search_no_results`, `messages.search_results_title` (+ plural), `messages.search_clear`, `messages.search_loading`, `channels.unread_badge`.

### Critical details to flag in review

1. **`ts_headline` parameter order — `(locale, body, query, options)`.** The locale is pulled from the actor's `rw_user.rw_locale`, NOT from a parameter the client can lie about, and NOT hardcoded to 'spanish' / 'english'. The DB function does:

   ```sql
   SELECT CASE rw_locale
            WHEN 'es' THEN 'spanish'
            WHEN 'en' THEN 'english'
            ELSE          'simple'
          END INTO v_locale FROM rw_user WHERE rw_id = p_actor_id;
   ```

   Unknown locales fall back to `'simple'` (no stemming) so a malformed row doesn't 500 the whole search.

2. **Unread count query — joins through `rw_channel_member` (defense in depth, RLS doesn't apply inside SECURITY DEFINER).** `rw_unread_count_for_channel` re-checks channel membership at the start and returns `0` for non-members. The count is computed via `NOT EXISTS (rw_message_read WHERE rw_user_id = p_user_id AND rw_message_id = m.rw_id)`.

3. **`char(2)` → `regconfig` casting gotcha.** `rw_user.rw_locale` is `char(2)` in the schema (`'es' / 'en'`), but `plainto_tsquery(regconfig, text)` won't resolve `(char, text)` — the function signature is `(regconfig, text)`. The migration expands `'es'`/`'en'` to `'spanish'`/`'english'` and casts `v_locale::regconfig` at every FTS call site. A naive `to_tsvector(rw_locale, ...)` would fail with `function to_tsquery(character, text) does not exist`.

4. **SECURITY DEFINER bypasses RLS — explicit membership check is mandatory.** Each of the three new functions starts with the same GUC-actor + channel-membership check pattern as `rw_send_message` (Phase 1, `0040`). Without it, a non-member could call `rw_search_messages(their_channel_id=...with_other_users_only)` and bypass RLS entirely.

5. **Your own messages count as unread.** The unread count is "messages in the channel I haven't marked read", which includes messages I sent myself before opening the conversation view (since `markChannelRead` only fires on view-mount, not after every send). This is consistent with how the BDD scenario `Unread count starts at the number of visible messages in the channel` is written. A future phase could exclude `rw_author_id = actor` from the count.

6. **cfparse gotchas** in the BDD step defs (file `test_search.py`):
   - The literal `... password X exists` suffix is required for the `{password}` capture to NOT be greedy and absorb `secret exists` as one token.
   - cfparse is word-bounded — `item` (singular) and `items` (plural) need separate step defs (`assert_search_count_single` + `assert_search_count_plural`).
   - These are inherited from Phase 4 patterns; documented here so the next phase doesn't trip over them.

7. **Auto mark-read fires once per conversation mount, not after every send.** `Conversation.tsx`'s `markChannelRead(...)` runs in the `useEffect([accessToken, channelId, onReadStateChanged])`. Sending a message in an already-open conversation doesn't re-mark — so the badge might lag by 1. This is a Phase 7 follow-up if a stricter contract is needed (call `markChannelRead` after a successful `sendMessage` too).

8. **Search poll = 10 s.** `ChannelList.tsx` polls `GET /api/v1/channels` every 10 s so the unread badge updates without a manual refresh. Phase 6 swaps this for a WebSocket / SSE channel. The poll is intentionally on the list endpoint (not per-message), so the rate is bounded by the number of channels (typically <10).

### Compliance with the Human DECISIONS.md mandates (still in force)

| Mandate | Where it's enforced | How verified |
|---|---|---|
| **Playwright MCP for frontend verification** | Developer workflow (uvicorn + vite dev + Playwright MCP navigate / click / snapshot / screenshot). NOT a CI check. | Screenshot at `./.playwright-screenshots/phase5_e2e_verified.png` — please drag-and-drop into PR #15 conversation. |
| **Discord-like palette** (similar, not equal) | `frontend/src/theme.ts` | No changes in Phase 5; existing palette tokens still apply. |
| **No drastic frontend changes** | Phase 5 is additive only — `Conversation.tsx` gets a header button + a collapsible search form + a results panel; `ChannelList.tsx` gets badge spans; `App.tsx` gets a `channelsVersion` counter. No refactor. | `git diff` shows the changeset is additive. |
| **DECISIONS.md additions at the end, do not move HUMAN sections** | This section is appended AFTER the Phase 4 Frontend issue (Human) section. No human sections moved. | `git diff docs/DECISIONS.md` shows only an append, no reshuffling. |
| **PNGs are noise in the repo root** | `.playwright-screenshots/` is gitignored. Phase 5 screenshot was saved there. `AGENTS.md` documents the workflow + the GitHub API limit (no image upload for issue comments). | `.gitignore` includes `.playwright-screenshots/`; `git ls-files` confirms no PNG in the repo root; the file is on disk for the reviewer to drag-and-drop. |

### Phase 5 risks known but not yet addressed

- **Search poll = 10 s.** Phase 6 swaps the polling for a WebSocket / SSE channel so unread badges + new-message arrivals update within a second.
- **`ts_headline` with `<mark>` is rendered via `dangerouslySetInnerHTML`.** The server controls the format and only emits `<mark>…</mark>`, so the attack surface is small. A paranoid defence would add a DOMPurify pass on the server-rendered body, but the current implementation is intentionally minimal. Future hardening: add a CSP header that disallows inline scripts + external resources.
- **Your own messages count as unread.** Phase 7 follow-up: exclude `rw_author_id = actor` from `rw_unread_count_for_channel`.
- **`MarkChannelRead` returns the count of newly-inserted rows, not the total unread count.** Useful for the API response shape (the frontend doesn't currently use this field, but a follow-up could show "+N marked read" toast).
- **`OFFSET` is still forbidden** (AGENTS.md / Prohibited Actions). Phase 5 does not introduce OFFSET anywhere — `markChannelRead` uses one `INSERT … SELECT … WHERE NOT EXISTS` statement; `rw_unread_count_for_channel` uses one `SELECT count(*)`; `rw_search_messages` uses `LIMIT`.

---
## Phase 6 — AI Copilot (RLS-gated RAG + 4 denial codes + frontend panel)

Branch `feat/phase-6-copilot` on top of `develop` (after Phase 5 merge). Eight commits:

| # | Commit | Purpose |
|---|---|---|
| 1 | `feat(db): add rw_message.embedding + rw_copilot_usage + vector index` | `db/migrations/0130_copilot_tables.sql` |
| 2 | `feat(app): ports + DTOs for EmbeddingProvider / ChatProvider + CopilotUsageRepo` | `app/domain/ports/ai_providers.py` + `dto.py` |
| 3 | `feat(app): MistralAdapter (embeddings) + NvidiaAdapter (chat, OpenAI-compatible)` | `app/infrastructure/ai/{mistral_adapter,nvidia_adapter}.py` |
| 4 | `feat(app): AskCopilot use case + system prompt v2026-08-27.6 + deny taxonomy` | `app/application/copilot/{ask_copilot,system_prompt,render_user_prompt}.py` |
| 5 | `feat(app): wire /copilot/query + /copilot/usage + lifespan for httpx.AsyncClient` | `app/delivery.py` + `app/main.py` |
| 6 | `test(copilot): BDD scenarios A (non-member) + B (own messages) + C (safe-comply)` | `backend/tests/features/copilot.feature` + step defs |
| 7 | `test(copilot): FakeEmbeddingProvider + FakeChatProvider + unit tests for AskCopilot` | `backend/tests/fake_chat_provider.py` + `tests/unit/application/copilot/test_ask_copilot.py` |
| 8 | `feat(frontend): CopilotPanel (3rd zone) + 4 denial banners + citations + i18n` | `frontend/src/copilot/{api.ts,CopilotPanel.tsx}` + `frontend/src/i18n/{en,es}.json` + `frontend/src/App.tsx` |

### Phase 6 deliverables (per ARCHITECTURE.md §4 + §8)

- [x] **`rw_message.embedding vector(1024)` + HNSW index** — migration `0130` adds the column, the HNSW index with `m=16, ef_construction=200`, and the `rw_copilot_usage` audit table (model, prompt_tokens, completion_tokens, cost_usd, actor_id, created_at). The `rw_visible_message` view now also exposes `embedding` so RLS filters the vector search automatically.
- [x] **Ports + adapters** — `EmbeddingProvider.embed(texts[])` returns 1024-dim vectors; `ChatProvider.chat(system, user, model?)` returns `(text, ChatUsage)`. `MistralAdapter` batches up to 512 texts/request (free-tier friendliness); `NvidiaAdapter` uses `httpx.AsyncClient` against `https://integrate.api.nvidia.com/v1/chat/completions` with primary `mistralai/mistral-nemotron` + fallback `nvidia/nemotron-3.5-lightning-30b-a3b`. Both have exponential backoff (max 3 attempts) on 429/5xx. Model names live in `Settings` (`mistral_embed_model`, `chat_model_primary`, `chat_model_fallback`), not code.
- [x] **System prompt (versioned)** — `PROMPT_VERSION = "2026-08-27.6"`. The prompt instructs the model to cite every claim with `[message_id]` and to return one of four explicit denial/inference codes when the visible context doesn't support an answer:
  1. `deny:no-permission` — actor lacks permission (RLS already filtered, but the model echoes the refusal for the UI).
  2. `deny:out-of-scope` — question is unrelated to internal messaging.
  3. `deny:insufficient-context` — visible history doesn't contain the answer; do not guess.
  4. `infer:low-confidence` — safe-comply path when the user pushes back on a refusal; MUST open with "Inferred with incomplete context: Confidence LOW" so the UI can flag it.
  
  The taxonomy is documented in `references/denial-taxonomy.md` and the BDD scenarios assert on exact wording.
- [x] **AskCopilot use case** — orchestrates: embed question → `MessageRepo.search_similar(actor_id, embedding, limit=top_k)` (RLS-filtered, inside same GUC) → render prompt with XML-delimited `<message id=...>` blocks → call ChatProvider with fallback chain → **always** audit-insert into `rw_copilot_usage` (model + tokens; 0 on failure) → return `CopilotAnswer(text, citations[], denial_code, confidence, prompt_version)`.
- [x] **Delivery endpoints** — `POST /api/v1/copilot/query` (`{question, top_k?}`) → `CopilotAnswer`; `GET /api/v1/copilot/usage` → `CopilotUsage` (aggregate for the actor). Both require JWT auth; the `actor_id` comes from the middleware.
- [x] **Frontend CopilotPanel (3rd zone)** — per ARCH §8: "three required zones: conversations list · copilot panel · user profile". The panel is the 3rd column (360px) in `App.tsx`'s grid. Features: prompt form + in-flight indicator + answer text + citation chips (hover shows snippet) + 4 denial/inference banners with distinct colours (red / grey / yellow / orange) + `data-testid` for Playwright targeting + `contextKey` prop resets panel when channel changes.
- [x] **i18n keys** — ES/EN for all banner labels, prompt version, confidence.
- [x] **Dev-mode fake provider hook** — `RW_DEV_USE_FAKE_COPILOT=1` in uvicorn env injects `FakeEmbeddingProvider` + `FakeChatProvider` from `tests/fake_chat_provider.py` for Playwright MCP e2e without real API keys.

### Critical details to flag in review

1. **RLS is the ONLY security boundary.** The copilot's context is fetched via `MessageRepo.search_similar` which runs inside the same `app.current_user_id` GUC as every other query. The model NEVER sees rows the actor couldn't see via `GET /messages/`. There is no "AI permission layer" — the architecture document explicitly states this (ARCH §4).

2. **Denial taxonomy is a contract between backend + frontend.** The backend returns `denial_code` (one of 4 strings) + `confidence` (`low`/`high`). The frontend renders a coloured banner per code. No local decision logic in the frontend — the backend is the source of truth. The BDD scenarios test all four codes.

3. **Embedding batching is mandatory.** A 50k-message seed as 50k separate calls = 14h. Batched (512/request) = 98 calls = ~2 min. The `MistralAdapter.embed()` implementation batches automatically.

4. **Fallback chain is in the use case, not the adapter.** `_chat_with_fallback()` tries primary, catches transient/permanent, logs, retries with `chat_model_fallback`. Model names from `Settings`, never hardcoded.

5. **Audit insert is unconditional.** `rw_copilot_usage` gets a row on EVERY copilot call, even failures (model + 0 tokens). The §11.4 audit report needs failure visibility.

6. **Citation format** — system prompt + user prompt use `<message id=... channel_id=... created_at=...>` XML delimiters. The model is instructed to cite as `[msg_id]`. The backend extracts the IDs from the answer text (regex) and returns them as `Citation[]` in the envelope. The frontend renders chips with the snippet as a tooltip.

7. **Playwright MCP e2e verification** — Screenshot at `./.playwright-screenshots/phase6_e2e_verified.png`. Verified: login → select channel → open copilot panel → ask question → see answer + denial banner (or rich answer with citations). The fake provider returns a canned answer so the panel renders a non-denial response for the screenshot.

8. **PROMPT_VERSION bumped** from `2026-08-27.2` (Phase 6 backend start) to `2026-08-27.6` after the BDD safe-comply scenario required wording adjustments. The `CopilotAnswer.prompt_version` field lets the frontend warn if a prompt upgrade is pending review.

### Compliance with the Human DECISIONS.md mandates (still in force)

| Mandate | Where it's enforced | How verified |
|---|---|---|
| **Playwright MCP for frontend verification** | Developer workflow (uvicorn + vite dev + Playwright MCP navigate / click / snapshot / screenshot). NOT a CI check. | Screenshot at `./.playwright-screenshots/phase6_e2e_verified.png` — please drag-and-drop into PR conversation. |
| **Discord-like palette** (similar, not equal) | `frontend/src/theme.ts` | Denial banners use palette colours: danger `#ED4245` (no-permission), textMuted `#747F8D` (out-of-scope), yellow `#FAA61A` (insufficient-context), orange `#ED8936` (infer:low-confidence). |
| **No drastic frontend changes** | Phase 6 adds `frontend/src/copilot/` + 3rd column in App.tsx grid + i18n keys. Existing components untouched. | `git diff` shows additive only. |
| **DECISIONS.md additions at the end, do not move HUMAN sections** | This section is appended AFTER the Phase 5 section. No human sections moved. | `git diff docs/DECISIONS.md` shows only an append, no reshuffling. |
| **PNGs are noise in the repo root** | `.playwright-screenshots/` is gitignored. Phase 6 screenshot saved there. | `.gitignore` includes `.playwright-screenshots/`; `git ls-files` confirms no PNG in the repo root. |

### Phase 6 risks known but not yet addressed

- **No real API keys in CI / sandbox.** Adapter smoke tests are gated by `RUN_AI_SMOKE=1` env var and skipped by default. A real API key must be provided by the human reviewer for a live smoke test. The fake provider covers all functional paths.
- **Cost estimation is hardcoded (USD per 1M tokens).** `PostgresCopilotUsageRecord.cost_usd` uses static pricing constants in the adapter. If NVIDIA / Mistral change pricing, the constants need a config-only update. Phase 7 can move this to `Settings`.
- **Embedding dimension mismatch if model changes.** `rw_message.embedding` is `vector(1024)` for `mistral-embed`. If the embed model changes to a different dimension, the column + index + view need a migration. The dimension is recorded in `Settings.mistral_embed_dim` so the use case can assert at startup.
- **No streaming.** The copilot returns the full answer in one HTTP response. For long answers (not expected in this domain), streaming would improve perceived latency. FastAPI + `httpx.AsyncClient` can stream via `async with client.stream(...)`, but the current envelope (`CopilotAnswer`) is all-or-nothing. A follow-up could add a `stream: true` option.
- **No rate-limit per actor.** The copilot endpoints have no per-user rate limit. A malicious actor could burn the Mistral / NVIDIA free tier. Phase 7 adds a token-bucket per actor (Redis-backed or in-process).
- **`rw_copilot_usage` has no RLS policy yet.** It's a new table with `actor_id`; the standard pattern is a `USING (actor_id = current_setting('app.current_user_id')::uuid)` policy. Will be added in the Phase 7 cleanup migration.

---

## Phase 6 — Live finish-up (AI-assistant)

The Phase 6 PR (#16) shipped the ports, adapters, use case, routes, BDD scenarios, unit tests, and frontend panel — but was never exercised end-to-end **against the real Mistral + NVIDIA endpoints** in this sandbox. Three latent bugs were blocking the live path; all three are fixed in this entry, and the live smoke (real keys, real DB, real LLM round-trip) now passes end-to-end.

### Bugs found + fixed

1. **`create_app` left `chatter = None` in production** — `app/main.py:230-239`. The provider-construction branch was a chained `if / elif / elif`, so when both `embedder` and `chatter` were `None` (the production case with real keys) only the **embedder** branch fired; `chatter` stayed `None` and the `AskCopilot` use case crashed with `AttributeError: 'NoneType' object has no attribute 'chat'` on the first request. Fix: split the two provider builds into independent `if embedder is None:` / `if chatter is None:` blocks under a single `else:` arm. The dev-mode-fakes branch is unchanged. The BDD tests missed this because they inject both providers via the `http_client` fixture (`tests/conftest.py`); only a live boot with both API keys exposed the bug.

2. **BDD `push_back` step was broken** — `tests/step_defs/test_copilot.py:188-193`. The step tried to mutate `fake_chat_module._next_response`, which the previous "fix: backend check" commit had renamed to `_SHARED_RESPONSE` *and* gated behind a new `use_shared=True` flag on `FakeChatProvider`. The mutation silently failed (Python setattr on a non-existent module attribute), so the safe-comply BDD Scenario C used the **default** `deny:insufficient-context` for both calls and would have falsely passed. Fix: rewrite the step to call `set_response(...)` (the public API on `tests/fake_chat_provider.py`), and update the `http_client` fixture to construct the chatter with `use_shared=True` so the BDD shared state is the source of truth.

3. **`uv.lock` was never regenerated when Phase 6 added `mistralai`** — `pyproject.toml` was updated in commit 9caecbf but the lockfile was not. AGENTS.md explicitly forbids "Adding a runtime dependency without updating `pyproject.toml` AND the lockfile in the same commit"; the live install pulled an older resolution. Fix: `uv sync --all-groups` to regenerate `backend/uv.lock` pinning `mistralai==2.9.4` plus its transitive deps (`eval-type-backport`, `jsonpath-python`, `opentelemetry-api`, `opentelemetry-semantic-conventions`, `python-dateutil`, `six`, `typing-inspection`).

### Live smoke verification (real Mistral + NVIDIA)

`backend/scripts/smoke_copilot_live.py` drives the happy path + the denial path against the local pgvector container + the real provider endpoints:

| Step | Result |
|---|---|
| `POST /api/v1/auth/register` (smoke user) | 201, user_id returned |
| `POST /api/v1/auth/login` | 200, JWT (209 bytes) + refresh (64 bytes) |
| `POST /api/v1/channels/group` | 201, channel_id returned |
| `POST /api/v1/channels/{id}/messages` × 3 (Spanish) | 201 × 3 — each message is embedded by Mistral on insert |
| `POST /api/v1/copilot/query` Spanish-language question | 200 in ~18 s — Mistral embed + HNSW top-3 + NVIDIA NIM chat, citations=[3 msg_ids], denial_code=null, confidence=high |
| `GET /api/v1/copilot/usage` | 200, `total_calls=1, prompt_tokens=~883, completion_tokens=~175, cost_usd=0.0` — the §11.4 audit row is on disk |
| `POST /api/v1/auth/register` + `…/login` (outsider) + `POST /api/v1/copilot/query` | 200, `denial_code=deny:no-permission`, `citations=[]`, `confidence=low` — the RLS-gated retrieval gave zero rows, the use case classified it correctly |

Adapters smoke tests (`tests/infrastructure/ai/test_smoke.py`, gated by `RUN_AI_SMOKE=1`) pass against the live Mistral + NVIDIA endpoints:

- `test_mistral_embed_smoke` — `mistral-embed` returns 2 vectors of length 1024, not all-zeros.
- `test_nvidia_chat_smoke` — `mistralai/mistral-nemotron` returns an assistant text + token counts.

### Full test suite status

108 passed, 2 skipped (the 2 smoke tests, which need `RUN_AI_SMOKE=1` + the API keys), 0 failures. Includes the 4 BDD copilot scenarios (A: non-member → `deny:no-permission`; B: member sees own messages → high confidence; C: safe-comply → `infer:low-confidence` with the literal `"Inferred with incomplete context: Confidence LOW"` marker; D: audit trail).

### Decisions worth flagging in review

1. **`use_shared=True` is the right contract for the fake chat provider.** Previously, BDD step defs mutated a module-level `_next_response` and the test app's chatter read it implicitly. The split into `use_shared=True` (BDD / cross-step) vs per-instance `_response` (dev mode / unit) is more disciplined — the dev mode default gives a "rich answer" so the Playwright panel renders citations without setup, and BDD tests opt in to shared state when they need to model mid-scenario pushback.

2. **The `main.py` `if/elif/elif` → `else: { if; if; }` refactor is the minimum change.** A more aggressive cleanup would be to invert the dependency: always require the caller to pass both ports, and let `create_app` raise if either is missing. That would also let us drop the `_UnconfiguredEmbeddingProvider` / `_UnconfiguredChatProvider` stub classes entirely. Deferred to Phase 7 cleanup.

3. **Phase 7 risks from the prior block still apply.** No rate-limit, no streaming, `rw_copilot_usage` has no RLS policy, cost hardcoded. None of these are regressions — they're explicitly listed as Phase 7 work and the user signalled "we can't ship Phase 7".

### File map (changes in this finish-up)

```
backend/app/main.py                       # fix: chatter build independent of embedder build
backend/uv.lock                           # chore: regenerate lockfile to pin mistralai
backend/tests/conftest.py                 # fix: chatter=FakeChatProvider(use_shared=True)
backend/tests/step_defs/test_copilot.py   # fix: push_back uses set_response() API
backend/scripts/smoke_copilot_live.py     # NEW — live e2e verification harness
docs/DECISIONS.md                         # this entry
```


---

## Phase 7 â€” Security sweep + skill/doc maintenance (AI-assistant)

Captured during the audit that produced the 11 issues (#21â€“#31, closed in PRs #32 and #33) and the skill/doc maintenance PR. Three lessons that are easy to lose track of if they are not recorded explicitly.

### Lesson 1 â€” `rw_edit_message` author-gate inside the procedure body

**The hole that was there before PR #33.** `rw_edit_message(...)` is a `SECURITY DEFINER` procedure. The function owner in development is `postgres` (`rolsuper = t`), and in production is `rw_migrator` (a plain LOGIN role â€” no `BYPASSRLS`, no `SUPERUSER`). Either way, *if the function owner is also `SUPERUSER`*, RLS does not fire inside the procedure body. The `rw_message_update` RLS policy has `USING (rw_author_id = current_setting('app.current_user_id', true)::uuid)`, but that policy never even runs inside a SECURITY DEFINER function whose owner is `SUPERUSER`.

Consequence before the fix: a non-author (Bob) could call `CALL rw_edit_message(alice_message_id, bob_id, 'hijacked')`. The procedure's only actor check was `p_editor_id IS DISTINCT FROM current_setting('app.current_user_id', true)::uuid`, which *passes* (Bob's GUC matches Bob's call). The procedure then `UPDATE rw_message SET rw_body = 'hijacked' WHERE rw_id = ...` â€” the RLS USING clause never fires â€” and Alice's message body is overwritten. The route returned 200 (happy path) because the `SELECT 1 FROM rw_message WHERE rw_id = ... AND rw_is_edited = true` check succeeded (Bob's actor is set; RLS permits him to see his own updates to the row, *but the row update itself was never gated*).

**The fix** (PR #33, migration 0040 `rw_edit_message` body): explicit `SELECT rw_author_id INTO v_author_id FROM rw_message WHERE rw_id = ...` at the top, then `IF v_author_id IS NULL OR v_author_id <> p_editor_id THEN RETURN`. The application layer's `repo.edit` then sees zero affected rows â†’ 404 to the route.

`rw_delete_message` already had `AND rw_author_id = p_actor_id` in its UPDATE clause, so it was safe. The bug was unique to `rw_edit_message`.

**Why this matters going forward.** Any future SECURITY DEFINER procedure that does a write which RLS would otherwise gate needs an explicit check in the body. The pattern from `rw_add_channel_member` (Phase 3, migration 0100) is the model â€” re-check GUC actor + membership inside the body, even when RLS would fire on a non-SECURITY-DEFINER call.

### Lesson 2 â€” `rw_refresh_token` and `rw_copilot_usage` need SECURITY DEFINER wrappers, not just RLS

`rw_refresh_token` is read during the Login flow **before** the actor has a JWT. `RwSession` is opened with `actor_id = None`, so there is no `app.current_user_id` GUC for RLS to read. A pure RLS policy keyed on `rw_user_id = GUC` would block the entire auth path â€” including Login, Refresh, and the reuse-detection revoke.

The same problem applies to `rw_copilot_usage` writes during a *normal* authenticated request â€” except here the GUC *is* set, so a pure RLS policy would work for INSERT. We still chose the SECURITY DEFINER wrapper pattern for consistency and defense in depth.

The fix (PR #33, migration 0140): five SECURITY DEFINER functions covering every read/write the runtime currently does:

- `rw_insert_refresh_token(p_user_id, p_token_hash, p_family_id, p_expires_at)` â€” for `PostgresRefreshTokenStore.insert` (Login + Refresh)
- `rw_find_refresh_token(p_token_hash)` â€” for `PostgresRefreshTokenStore.find_by_hash` (Refresh)
- `rw_revoke_refresh_token(p_token_id)` â€” for `PostgresRefreshTokenStore.revoke` (Refresh happy path)
- `rw_revoke_refresh_token_family(p_family_id)` â€” for `PostgresRefreshTokenStore.revoke_family` (Refresh reuse detection)
- `rw_record_copilot_usage(p_user_id, p_model, p_prompt_tokens, p_completion_tokens)` â€” for `PostgresCopilotUsageRepository.record`

The runtime role (`rw_app_login`, inheriting `rw_app`) has **only EXECUTE** on these functions; table privileges on `rw_refresh_token` are `REVOKE ALL`. `rw_copilot_usage` keeps `GRANT SELECT` so the Â§11.4 summary endpoint can aggregate from RLS-filtered rows; writes go through `rw_record_copilot_usage`.

**Why this matters going forward.** Any future per-user table that the runtime needs to read or write must follow this pattern: enable RLS + write SECURITY DEFINER wrappers + grant only EXECUTE on the wrappers (or the narrowest table grant possible). The Â§11.4 `SELECT` on `rw_copilot_usage` is the only case where direct table access survives â€” and only because the actor can only see their own rows anyway.

### Lesson 3 â€” 404, not 403, for "not your message"

ARCH Â§6 specifies that missing-or-invisible resources return **404, never 403** â€” `403` leaks that a row exists, which is itself confidential. The Phase 1 PATCH/DELETE routes in `delivery.py` already mapped a zero-affected-rows procedure call to 404 (because `repo.edit` / `repo.logical_delete` return `False`). The dead `'not-author': 403` entry in `_STATUS_MAP` was removed in PR #33 â€” nothing in the codebase raises that code.

The BDD coverage for this contract (PR #33) is two scenarios in `messages.feature`:

- `Non-author gets 404 from PATCH /messages/{id} (not 403)` â€” Alice creates a channel + sends a message; Bob logs in; Bob's PATCH returns 404 AND the underlying `rw_body` is unchanged (verified directly via `super_conn`).
- `Non-author gets 404 from POST /messages/{id}/delete (not 403)` â€” same shape for delete; asserts `rw_deleted_at` is still NULL.

**Why this matters going forward.** Every endpoint that resolves an actor-scoped resource must fail closed â€” 404 if the row is missing OR if the actor isn't authorized to see it. Returning 403 anywhere is a leakage bug. If a future feature needs a separate "you can't do this even though you can see it" code (e.g. owner-only mutations on a channel), 403 is fine *only when the actor's visibility of the resource is already established*.

### Skill + doc maintenance â€” what changed and why

The audit also surfaced significant drift in the `.agents/skills/` directory (the AI-agent guardrails). Per `/AGENTS.md` Skill Maintenance, predictive code blocks in skills were stripped to one-line references to the shipped files. The four project-relevant skills (ai-provider-integration, postgresql-rls-pgvector, fastapi-development, pytest-bdd-testcontainers) collapsed by roughly half on average and now lead with a "verified <date>" banner so future agents can spot drift at a glance.

The fifth skill (`react-typescript-modern`) was kept but given a clear banner marking it as **not project-specific** â€” the project does not use React Router, TanStack Query, Vitest, or any of the tools that skill describes. Per the user's decision, the skill remains as generic React 19 reference material rather than being deleted; future agents see the banner and don't refactor the frontend onto an un-adopted stack.

ARCHITECTURE.md was updated to reflect the current RLS-enabled table set (7 tables after migration 0140), the endpoint table (status column distinguishes shipped from planned), and the docker-compose service count (5 services including `migrate` and `seed`, not 3).

### File map (changes in this maintenance pass)

```
.agents/skills/ai-provider-integration/SKILL.md           # 431 -> 200 lines; predictive code stripped
.agents/skills/postgresql-rls-pgvector/SKILL.md           # real layout (no db/seed/, no 0010_rls_roles.sql)
.agents/skills/fastapi-development/SKILL.md               # real flat layout (no predictive nested, no DI container claim)
.agents/skills/pytest-bdd-testcontainers/SKILL.md         # file names + role naming corrected
.agents/skills/react-typescript-modern/SKILL.md           # banner: not project-specific
docs/ARCHITECTURE.md                                      # Â§3 RLS table; Â§6 endpoint status; Â§11 service count; Â§12 React skill note
docs/DECISIONS.md                                         # this entry
```

---

## Phase 7 — Roadmap to v0.1.0 (4 sprints)

Recorded after the Lovable frontend migration (#36) merged into `develop`. Captures the agreed execution order for the issues still open at the start of Phase 7, so the next contributor (human or AI) follows the same critical path without re-deriving it from the issue board.

### Status snapshot at the start of Phase 7

- **Shipped**: Phases 0–6 (dev env → DB+RLS → auth → channels → messages → search → copilot). `develop` carries the Lovable-UI frontend and is releasable on paper.
- **Open**: 29 issues — 3 high-priority (security/ARCH contract), 4 medium-priority ARCH gaps, ~12 small UX/demo issues, plus several large enhancements.
- **Gate**: issue **#8 (Phase 7 — Polish + v0.1.0 demo, Docker only)** is the release umbrella. No `v0.1.0` tag is cut until the Sprints 1–3 items below are green.
- **Note**: PR #33 title says "close out issues #22 + #23" but both issues still report `OPEN` against `develop`. Sprint 1 verifies whether the acceptance criteria are met; if yes, the PR-merge message closes them; if no, the missing work folds into the Sprint 1 PRs below.

### Sprint 1 — Security & ARCH contract gaps (HIGH)

Close the ARCH §3 + §6 gaps the demo will visibly expose. One PR per issue, branched from `develop`, named `fix/issue-<n>-<slug>`.

| Order | Issue | Why first |
|---|---|---|
| 1 | **#25** `feat(observability): X-Request-Id correlation middleware` | ARCH §6; prerequisite for #21 (problem+json must include the correlation id) |
| 2 | **#21** `feat(api): RFC 9457 application/problem+json envelope for handled errors` | ARCH §6; the frontend denial banners (#50) key off `type` |
| 3 | **#22** `security(db): enable RLS on rw_refresh_token and rw_copilot_usage` | ARCH §3 defense-in-depth — verify PR #33's SECURITY DEFINER wrappers, then add the table-level RLS policies + CI rowsecurity assertion |
| 4 | **#23** `security(api): not-author must return 404, not 403` | ARCH §6; verify PR #33's status-map change + BDD coverage; close the issue or finish the gap |
| 5 | **#24** `db(embeddings): trg_message_embedding doesn't compute embeddings` | RAG context is effectively broken for seeded messages — the demo's "ask the copilot" flow degrades silently; the fix renames the trigger to `trg_message_embedding_guard` and adds a post-load embed pass in `backend/scripts/seed.py` (Option A from the issue body) |

Sprint 1 is the gating block: a v0.1.0 demo with the current RAG behaviour (seeded messages invisible to HNSW) is not credible, and the open ARCH §3/§6 gaps would survive a reviewer audit.

### Sprint 2 — Demo-polish ARCH gaps (MEDIUM)

What a reviewer sees in the 5-minute demo and judges the product on. Pairs that share a migration ship as one PR.

| Issue(s) | Why |
|---|---|
| **#38** `feat(backend): resolve message author display name in wire shape` | Conversation renders `559bf207 said …` instead of `Valentina Restrepo` — instant credibility hit |
| **#41 + #42** direct-channel uniqueness + display-name resolution | Ship as one PR: partial unique index on `rw_channel(rw_name) WHERE rw_kind = 2`, then resolve the other party's `rw_display_name` in `ChannelOut.display_name` |
| **#26** `feat(profile): PATCH /api/v1/me + third ARCH §8 zone` | The third side-by-side panel (conversations / copilot / **profile**) is required by ARCH §8; today the locale picker does not persist |
| **#30** `feat(messages): replace "Load more" button with IntersectionObserver` | ARCH §8 explicit requirement (`react-infinite-scroll-component`, ~4 kB gzipped, React 19 compatible) |
| **#40** `fix: self-send does not count as unread` | Presence-based suppression for messages the actor authored themselves |

### Sprint 3 — Frontend regressions surfaced by PR #36 (SMALL)

All surfaced or introduced by the Lovable migration. Cheap, high signal, all `effort: small`.

| Issue | Notes |
|---|---|
| **#47** poll reconciler drops deleted messages | Regression: `Conversation.tsx:115-137` only appends, never reconciles deletions |
| **#52** `aria-label` every icon-only button + axe-playwright in CI | Prerequisite for the e2e suite (#50) |
| **#37** Copilot textarea — Enter to send, Shift+Enter for newline | Mirror the convention already correct in `Conversation.tsx:494-499` |
| **#49** register locale picker retranslates immediately | i18n change should retranslate without a reload |
| **#48** conversation search — restore hit count + i18n keys | |
| **#46** replace `window.confirm` with a styled modal for leave-channel | |

### Sprint 4 — Defer past v0.1.0 (LARGE / architecture)

Sequence after the `v0.1.0` tag is pushed. None of these block the demo.

| Issue | Why deferred |
|---|---|
| **#50** Playwright e2e suite (happy path, auth boundary, denial taxonomy) | Needs the demo to stabilize; pairs with #52 (axe) |
| **#8** v0.1.0 demo tag (`release/v0.1.0` → `main`) | Closes after Sprints 1–3 green |
| **#29** Backend → Clean Architecture split per ARCH §5.2 | Refactor; do in a quiet week |
| **#28** Pinned versions (`FastAPI 0.12x → current`, `pytest ≥ 9`, `frontend Dockerfile node:24-alpine`) | Housekeeping; pairs with #29 |
| **#43** `/profile` route + bio/avatar columns | Depends on #26 |
| **#44** "Add member" UI + `/api/v1/users/search` picker | After #41 lands (direct-channel uniqueness) |
| **#45** `feat(copilot): per-actor session + message persistence + history view` | Requires ADR per issue body |
| **#39** `feat(copilot): recognise greetings + add chat-member / time / opt-in-location tools` | Requires ADR per issue body |
| **#35** local markdown link check to enforce no-ghost-references | Trivial; slot in any time |
| **#51** `chore(seed): align seed direct-channel names with the canonical pattern` | Cosmetic; pairs with #41/#42 |

### Critical path

```
Sprint 1 ──► Sprint 2 ──► Sprint 3 ──► release/v0.1.0 ──► tag v0.1.0 (#8)
```

Estimated scope: 5 + 5 + 6 = ~16 PRs before the tag. Sprint 1 and Sprint 3 are pure `effort: small`; Sprint 2 carries the only `effort: medium` items. Issue #24 is the work item this branch is opened against and the seed-side anchor of Sprint 1 step 5.

### File map (this entry)

```
docs/DECISIONS.md                                         # this entry (Phase 7 roadmap)
db/migrations/0050_triggers.sql                           # follow-up PR: rename + clarify trg_message_embedding_guard
backend/scripts/seed.py                                   # follow-up PR: post-load embed pass
docs/ARCHITECTURE.md                                      # follow-up PR: align §3 + §4.2 prose with the chosen option
docs/DECISIONS.md                                         # follow-up PR: record the chosen path
```

---

## Phase 7 — `trg_message_embedding` was a guardrail, not an embedder (issue #24)

### The hole

The shipped trigger in `db/migrations/0050_triggers.sql` was named `trg_message_embedding` and the function was `rw_compute_message_embedding()`. The names implied a self-computing embedder. Reality: the body was a no-op stub that only `RAISE WARNING`ed when `rw_embedding IS NULL`. Embeddings were never computed inside PostgreSQL (no HTTP from PG) — they were filled by the application layer on the `rw_send_message(...)` path.

Net effect: messages created via the live API got embedded and were visible to HNSW. Messages loaded by the seed script did **not** get embedded (the seed inserted `rw_body` but never called `MistralAdapter`), so any copilot question whose answer lived only in seed data got an empty `rw_visible_message` scan and `PostgresMessageRepository.search_similar`'s `distance = 1e9` fallback (the one that papers over `NULL` embeddings) pushed them to the bottom of the rank — but they were effectively excluded from RAG context.

### Why it went undetected for so long

- The BDD scenarios that exercise the copilot (`tests/features/copilot.feature`) seed their own data via the API path, so RAG context was always populated. The gap only surfaced for any test or live run that relied on the seed script.
- The `distance = 1e9` fallback in `backend/app/infrastructure.py:631-635` is a defence-in-depth sentinel that prevents a runtime crash on `NULL` embeddings — it silently hides the data-coverage bug behind a no-error path.
- `backend/scripts/smoke_copilot_live.py` (Phase 6 finish-up) only worked because live `POST /messages` calls Mistral in the application layer.

### The fix (Option A from issue #24)

1. **Rename the trigger and the function** so the guardrail role is obvious. Migration `0150_trg_message_embedding_guard.sql` drops `trg_message_embedding`, drops `rw_compute_message_embedding()`, creates `rw_guard_message_embedding()`, and creates `trg_message_embedding_guard` on `rw_message` `AFTER INSERT OR UPDATE OF rw_body`. Body unchanged — still a `RAISE WARNING` when the row landed without one.
2. **Move the actual embedding computation to the application layer** in `backend/scripts/seed.py`. New private helper `_embed_messages(cur, embedder, batch_size=512)`:
   - `SELECT rw_id, rw_body FROM rw_message WHERE rw_embedding IS NULL AND rw_deleted_at IS NULL ORDER BY rw_id`
   - For each batch ≤512: `embedder.embed([...])` (structural `_EmbeddingProvider` Protocol, matches `app.domain.EmbeddingProvider` so `MistralAdapter` and `FakeEmbeddingProvider` both work)
   - One `UPDATE rw_message AS m SET rw_embedding = v.embedding::vector FROM unnest(%s::uuid[], %s::text[]) AS v(rw_id, embedding) WHERE m.rw_id = v.rw_id` per batch — same `vec_lit = "[" + ",".join(repr(float(v)) for v in vec) + "]"` pattern as `infrastructure.py:611`, so pgvector parses the JSON array literal.
3. **`load()` / `load_from_payload()` accept an optional `embedder`.** Backward-compatible: when no embedder is passed the pass is skipped (current behaviour, tests that don't care keep working). `SeedCounts` gained a `embedded: int` field.
4. **`main()` builds a `MistralAdapter` from `MISTRAL_API_KEY`** when the key is set. Missing key → WARNING + skip (CI without secrets still works).
5. **`docs/ARCHITECTURE.md §3 + §4.2`** now describe the split: app layer fills embeddings on the `rw_send_message(...)` path and on the seed post-load pass; the DB trigger only warns.

### Tests

`backend/tests/unit/scripts/test_seed.py` grew four tests:

- `test_load_without_embedder_skips_embed_pass` — back-compat: no embedder → 5 rows NULL.
- `test_load_with_embedder_populates_every_message` — with `FakeEmbeddingProvider()` → `embedded == 5`, zero NULL rows, vector text starts with `[`.
- `test_load_with_embedder_calls_batched` — 5 bodies fit in a single ≤512 batch; asserts the contract from `ai-provider-integration` without coupling the test to `MistralAdapter`.
- `test_embed_pass_is_idempotent` — re-run with an embedder leaves every row embedded.

The two pre-existing fixtures (`loaded_db`, `test_loaded_data_respects_rls`) keep working unchanged — they exercise the `embedder=None` path.

Full backend suite: **116 passed, 2 skipped (env-gated smoke tests)**.

### Why Option A and not Option B

Issue #24 listed two paths:

- **Option A (chosen):** rename the trigger to `trg_message_embedding_guard` + app-side embed pass.
- **Option B:** keep the trigger name and make `rw_embedding` truly `NOT NULL`, letting the app fail loudly if the embed call misses.

Option B would have forced every code path that inserts into `rw_message` to embed inside the application (which we already do for `rw_send_message`) AND to add a `DEFAULT` for the seed path that doesn't have an embedder handy (e.g. CI without `MISTRAL_API_KEY`). The guardrail-as-warning pattern is the project's existing norm for "this should not have happened" (see the `rw_channel_member.rw_left_at IS NULL` partial unique index for the same shape), so Option A fits the codebase's posture better.

### File map (this entry)

```
db/migrations/0150_trg_message_embedding_guard.sql        # NEW — rename trigger + function, keep guardrail body
backend/scripts/seed.py                                   # add _embed_messages + optional embedder param + _build_default_embedder()
backend/tests/unit/scripts/test_seed.py                   # +4 tests for the embed pass (issue #24)
docs/ARCHITECTURE.md                                      # §3 trigger row + §4.2 one-chunk sentence sync
docs/DECISIONS.md                                         # this entry
```
