"""Seed loader — Bronze → Silver + post-load embed pass (ARCHITECTURE.md §9).

Dev-only tool: populates a fresh PostgreSQL database from `db/seed/seed.json`. The flow is the medallion layering from the architecture:

    seed.json (denormalized)
        │
        ▼
    stg_seed_message (Bronze — payload as jsonb, row per load)
        │
        ▼
    rw_user, rw_channel, rw_channel_member, rw_message (Silver — 3FN)
        │
        ▼
    rw_embedding populated by the post-load embed pass (Gold — Phase 7)
        ▼
    rw_visible_message + embeddings + usage aggregates

The application layer fills `rw_embedding` for live messages on the `rw_send_message(...)` path; the seed loader fills it via a post-load pass through an injected `EmbeddingProvider` (matches `app.domain.EmbeddingProvider` so `FakeEmbeddingProvider` works in tests and `MistralAdapter` works in dev/CI with a real API key).

Usage (from the project root, with the backend venv active):

    MISTRAL_API_KEY=... DATABASE_URL=postgresql://postgres:postgres@localhost:5433/db_santiago_sanchez_nakamoto \\
        uv run python -m scripts.seed
    uv run python backend/scripts/seed.py

The script is intentionally idempotent: it TRUNCATEs the rw_* tables + stg_seed_message before re-populating, so re-running against a populated DB is a no-op-plus-reload (the dev workflow). The loader connects as the superuser (postgres) because it needs TRUNCATE on `stg_seed_message`, which is intentionally NOT granted to `rw_app` (Bronze is a dev-only artifact).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import HashingError


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_PATH = PROJECT_ROOT / "db" / "seed" / "seed.json"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("seed")


# ---------------------------------------------------------------------------
# Embedding port — structural Protocol keeps the loader independent of the
# Mistral SDK so unit tests can inject FakeEmbeddingProvider.
# ---------------------------------------------------------------------------
class _EmbeddingProvider(Protocol):
    """Structural subset of `app.domain.EmbeddingProvider`.

    Kept local on purpose: importing `app.domain` would force the loader to load the full app stack (FastAPI / psycopg pool / settings). The Protocol only declares what `_embed_messages` calls.
    """

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# Public dataclass — what load() returns
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SeedCounts:
    users: int
    channels: int
    memberships: int
    messages: int
    embedded: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load(
    conn: psycopg.Connection,
    seed_path: Path = DEFAULT_SEED_PATH,
    embedder: _EmbeddingProvider | None = None,
) -> SeedCounts:
    """Read seed.json from disk, load Bronze + Silver, return row counts.

    Idempotent: TRUNCATEs the rw_* tables (CASCADE handles FKs) and stg_seed_message before re-populating. The caller is responsible for committing or rolling back.
    """
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    return load_from_payload(conn, payload, embedder=embedder)


def load_from_payload(
    conn: psycopg.Connection,
    payload: dict[str, Any],
    embedder: _EmbeddingProvider | None = None,
) -> SeedCounts:
    """Same as `load`, but takes the parsed JSON in-memory (test-friendly)."""
    if not {"users", "channels"}.issubset(payload):
        raise ValueError(
            "seed.json must contain top-level 'users' and 'channels' arrays"
        )

    hasher = PasswordHasher()  # argon2id default cost

    with conn.cursor() as cur:
        # Bronze: the entire payload as a single jsonb row. Idempotent via TRUNCATE — there's only ever one Bronze row.
        cur.execute("TRUNCATE stg_seed_message RESTART IDENTITY")
        cur.execute(
            "INSERT INTO stg_seed_message (rw_payload) VALUES (%s)",
            (json.dumps(payload),),
        )

        # Silver: TRUNCATE in FK-safe order (CASCADE handles the rest). The rw_* tables are the source of truth; the seed loader is the only way data lands there in dev.
        cur.execute(
            "TRUNCATE rw_message, rw_message_edit, rw_message_read, "
            "rw_channel_member, rw_channel, rw_user, "
            "rw_auth_credential, rw_refresh_token, rw_copilot_usage "
            "RESTART IDENTITY CASCADE"
        )

        user_ids = _insert_users(cur, payload["users"], hasher)
        channel_ids = _insert_channels(cur, payload["channels"], user_ids)
        memberships = _insert_memberships(
            cur, payload["channels"], user_ids, channel_ids
        )
        messages = _insert_messages(cur, payload["channels"], user_ids, channel_ids)

        embedded = _embed_messages(cur, embedder) if embedder is not None else 0

    return SeedCounts(
        users=len(user_ids),
        channels=len(channel_ids),
        memberships=memberships,
        messages=messages,
        embedded=embedded,
    )


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------
def _insert_users(
    cur: psycopg.Cursor,
    users: list[dict[str, Any]],
    hasher: PasswordHasher,
) -> dict[str, Any]:
    """Insert into rw_user + rw_auth_credential; return {username: user_id}."""
    out: dict[str, Any] = {}
    for u in users:
        username = u["username"]
        cur.execute(
            "INSERT INTO rw_user (rw_username, rw_display_name, rw_locale) "
            "VALUES (%s, %s, %s) RETURNING rw_id",
            (username, u["display_name"], u["locale"]),
        )
        (user_id,) = cur.fetchone()
        out[username] = user_id

        # Argon2id hash of the dev password. Stored separately so the application layer can compare without seeing the plain text.
        try:
            pw_hash = hasher.hash(u["password"])
        except HashingError as exc:  # pragma: no cover — argon2 backend failure
            raise RuntimeError(f"argon2 hashing failed for {username}") from exc
        cur.execute(
            "INSERT INTO rw_auth_credential (rw_user_id, rw_password_hash) "
            "VALUES (%s, %s)",
            (user_id, pw_hash),
        )
    return out


_KIND_MAP = {"direct": 1, "group": 2}


def _insert_channels(
    cur: psycopg.Cursor,
    channels: list[dict[str, Any]],
    user_ids: dict[str, Any],
) -> dict[str, Any]:
    """Insert into rw_channel; return {channel_name: channel_id}.

    The seed.json uses human-readable kind strings ('direct' / 'group');
    the schema stores smallint (1 / 2). The mapping lives here so the
    JSON stays readable.
    """
    out: dict[str, Any] = {}
    for c in channels:
        owner_id = user_ids[c["owner"]]
        kind_int = _KIND_MAP[c["kind"]]
        cur.execute(
            "INSERT INTO rw_channel (rw_name, rw_kind, rw_created_by) "
            "VALUES (%s, %s, %s) RETURNING rw_id",
            (c["name"], kind_int, owner_id),
        )
        (channel_id,) = cur.fetchone()
        out[c["name"]] = channel_id
    return out


def _insert_memberships(
    cur: psycopg.Cursor,
    channels: list[dict[str, Any]],
    user_ids: dict[str, Any],
    channel_ids: dict[str, Any],
) -> int:
    """Insert into rw_channel_member; return total membership rows."""
    total = 0
    for c in channels:
        channel_id = channel_ids[c["name"]]
        owner_username = c["owner"]
        for member_username in c["members"]:
            role = 2 if member_username == owner_username else 1  # 2=owner, 1=member
            cur.execute(
                "INSERT INTO rw_channel_member "
                "(rw_channel_id, rw_user_id, rw_role) VALUES (%s, %s, %s)",
                (channel_id, user_ids[member_username], role),
            )
            total += 1
    return total


def _insert_messages(
    cur: psycopg.Cursor,
    channels: list[dict[str, Any]],
    user_ids: dict[str, Any],
    channel_ids: dict[str, Any],
) -> int:
    """Insert into rw_message; return total messages inserted."""
    total = 0
    for c in channels:
        channel_id = channel_ids[c["name"]]
        for m in c["messages"]:
            cur.execute(
                "INSERT INTO rw_message "
                "(rw_channel_id, rw_author_id, rw_body, rw_created_at) "
                "VALUES (%s, %s, %s, %s)",
                (
                    channel_id,
                    user_ids[m["author"]],
                    m["body"],
                    _parse_iso(m["created_at"]),
                ),
            )
            total += 1
    return total


def _parse_iso(value: str) -> datetime:
    """Accept the common ISO 8601 forms in seed.json ('Z' suffix included)."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Post-load embed pass
# ---------------------------------------------------------------------------
def _embed_messages(
    cur: psycopg.Cursor,
    embedder: _EmbeddingProvider,
    batch_size: int = 512,
) -> int:
    """Populate `rw_embedding` for every seeded message that lacks one.

    Batches up to `batch_size` bodies per `embedder.embed([...])` call (the Mistral free-tier throughput lever — `MistralAdapter.BATCH_LIMIT` is the same value, but kept local so the loader doesn't import the app stack). Updates land in one `UPDATE … FROM unnest(...)` per batch — no per-row round-trip.

    Returns the number of messages whose `rw_embedding` was filled.
    """
    cur.execute(
        "SELECT rw_id, rw_body FROM rw_message "
        "WHERE rw_embedding IS NULL AND rw_deleted_at IS NULL "
        "ORDER BY rw_id"
    )
    pending = cur.fetchall()
    if not pending:
        return 0

    updated = 0
    for start in range(0, len(pending), batch_size):
        chunk = pending[start : start + batch_size]
        ids = [str(row[0]) for row in chunk]
        bodies = [row[1] for row in chunk]
        embeddings = embedder.embed(bodies)
        vec_lits = [
            "[" + ",".join(repr(float(v)) for v in vec) + "]"
            for vec in embeddings
        ]
        cur.execute(
            "UPDATE rw_message AS m "
            "SET rw_embedding = v.embedding::vector "
            "FROM unnest(%s::uuid[], %s::text[]) AS v(rw_id, embedding) "
            "WHERE m.rw_id = v.rw_id",
            (ids, vec_lits),
        )
        updated += cur.rowcount
    return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_default_embedder() -> _EmbeddingProvider | None:
    """Build a `MistralAdapter` from `MISTRAL_API_KEY`, or None.

    Returns `None` when the key is missing — the loader logs a WARNING and continues without embeddings. Production seed runs must set the key (seeded messages must be visible to HNSW).
    """
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        return None
    try:
        from app.infrastructure import MistralAdapter
    except ImportError as exc:  # pragma: no cover — mistralai SDK missing
        raise RuntimeError(
            "MISTRAL_API_KEY is set but MistralAdapter could not be imported; "
            "install mistralai (`uv add mistralai`) or unset the key to skip "
            "the embed pass"
        ) from exc
    return MistralAdapter(api_key=api_key)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    # The loader needs DDL privileges (TRUNCATE on stg_seed_message + the rw_* tables) so it connects as the superuser. Dev-only — in production, the seed loader is not run; the corp load is a one-shot ETL job using a role that has been granted TRUNCATE on stg_seed_message.
    dsn = os.environ.get(
        "SEED_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5433/db_santiago_sanchez_nakamoto",
    )
    seed_path = Path(os.environ.get("SEED_PATH", str(DEFAULT_SEED_PATH)))

    embedder = _build_default_embedder()
    if embedder is None:
        logger.warning(
            "MISTRAL_API_KEY not set — skipping the post-load embed pass. "
            "Seeded messages will land with rw_embedding IS NULL and the "
            "copilot will skip them (trg_message_embedding_guard will "
            "emit a WARNING per row). Set MISTRAL_API_KEY to populate "
            "embeddings during seed."
        )

    logger.info("Connecting to %s", _redact_dsn(dsn))
    with psycopg.connect(dsn, autocommit=False) as conn:
        counts = load(conn, seed_path, embedder=embedder)
        conn.commit()

    logger.info(
        "Loaded %d users, %d channels, %d memberships, %d messages (%d embedded)",
        counts.users,
        counts.channels,
        counts.memberships,
        counts.messages,
        counts.embedded,
    )
    return 0


def _redact_dsn(dsn: str) -> str:
    """Hide the password in a DSN for logging."""
    if "@" not in dsn:
        return dsn
    head, tail = dsn.split("@", 1)
    if ":" not in head:
        return dsn
    scheme_user, _password = head.split(":", 1)
    scheme = scheme_user.split("://", 1)[0]
    return f"{scheme}://***:***@{tail}"


if __name__ == "__main__":
    sys.exit(main())
