-- Phase 0 — 0001_extensions.sql
-- Enable pgcrypto (gen_random_uuid, crypt) and vector (pgvector embeddings).
-- Both extensions ship with the pgvector/pgvector:pg18 image; this migration
-- is idempotent and runs as a docker-entrypoint-initdb.d script.
--
-- See ARCHITECTURE.md §2.1 and AGENTS.md / Prohibited Actions.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
