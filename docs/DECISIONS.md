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
