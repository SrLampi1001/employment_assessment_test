"""Step definitions for tests/features/membership.feature.

The two mandatory BDD scenarios from ARCHITECTURE.md §10, automated
against a real pgvector testcontainer running as `rw_app_login`
(rw_app with NOLOGIN, no BYPASSRLS).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
from pytest_bdd import given, scenarios, then, when

from ..conftest import (
    CAMILA,
    CHANNEL_PRIVATE,
    CHANNEL_TEAM1,
    VALENTINA,
    as_actor,
)

# pytest-bdd 8.x removed auto-discovery of feature files. Explicitly bind
# the two mandatory scenarios from ARCHITECTURE.md §10 to this module.
_FEATURE_FILE = Path(__file__).resolve().parents[1] / "features" / "membership.feature"
scenarios(str(_FEATURE_FILE))


# ─── Scenario A: Non-member ──────────────────────────────────────────────


@given('user "Valentina" who is not a member of channel "Camila-private"')
def valentina_not_in_camila_private() -> None:
    """Dataset is seeded by the autouse `_seed` fixture in conftest.py:
    Valentina is a member of team-1 but NOT of Camila-private."""
    # No-op here; documented so the Gherkin step is wired to a step def.
    assert True


@given('a message sent in "Camila-private" by user "Camila"')
def camila_sent_in_camila_private() -> None:
    """The seed fixture inserts exactly one such message."""
    assert True


@when("Valentina requests the channel history")
def valentina_reads_history(actor_conn: psycopg.Connection) -> None:
    as_actor(actor_conn, VALENTINA)
    actor_conn.commit()  # set_config outside a tx; the next SELECT starts one
    with actor_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_id, rw_channel_id, rw_body FROM rw_visible_message"
        )
        actor_conn._history_rows = cur.fetchall()


@when("Valentina runs a messages search")
def valentina_runs_search(actor_conn: psycopg.Connection) -> None:
    as_actor(actor_conn, VALENTINA)
    actor_conn.commit()
    with actor_conn.cursor() as cur:
        # ES-locale search, mirroring ARCHITECTURE.md §4.2 + §6.
        cur.execute(
            "SELECT rw_id, rw_body FROM rw_visible_message "
            "WHERE to_tsvector('spanish', rw_body) "
            "  @@ plainto_tsquery('spanish', %s)",
            ("Camila",),
        )
        actor_conn._search_rows = cur.fetchall()


@when("Valentina asks the copilot")
def valentina_asks_copilot(actor_conn: psycopg.Connection) -> None:
    """Vector search path — ARCHITECTURE.md §4.1.

    A real 1024-dim embedding is out of scope here; a zero vector is
    cos-equivalent and exercises the same RLS predicate (the policy is
    on the row, not on the embedding value). The literal is cast to
    `vector(1024)` in SQL so we don't need the pgvector Python types
    in test dependencies.
    """
    as_actor(actor_conn, VALENTINA)
    actor_conn.commit()
    zero_vec = "[" + ",".join(["0"] * 1024) + "]"
    with actor_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_id, rw_channel_id, rw_author_id FROM rw_visible_message "
            "ORDER BY rw_embedding <=> CAST(%s AS vector(1024)) LIMIT 5",
            (zero_vec,),
        )
        actor_conn._copilot_rows = cur.fetchall()


@then("the message does not appear in any of the three channels")
def camila_private_message_not_visible(actor_conn: psycopg.Connection) -> None:
    history = getattr(actor_conn, "_history_rows", [])
    search = getattr(actor_conn, "_search_rows", [])
    copilot = getattr(actor_conn, "_copilot_rows", [])

    # Sanity: the history should still show Valentina's team-1 message,
    # proving RLS is filtering channel-by-channel, not returning zero rows.
    assert any("Valentina" in row[2] for row in history), (
        "Valentina should see her own team-1 message; if she sees zero, "
        "the policy is too aggressive."
    )

    # The Camila-private message must be invisible in every read path.
    # Each path checks via the channel id (most robust — we already have
    # the row id from the search path) rather than the row id (which is
    # regenerated each run by gen_random_uuid()).
    history_channels = {row[1] for row in history}
    search_bodies = {row[1] for row in search}
    copilot_channels = {row[1] for row in copilot}

    assert str(CHANNEL_PRIVATE) not in map(str, history_channels), (
        f"History leaked the private channel: {history!r}"
    )
    assert "Este es un mensaje privado de Camila" not in search_bodies, (
        f"Search leaked the private message body: {search!r}"
    )
    assert str(CHANNEL_PRIVATE) not in map(str, copilot_channels), (
        f"Copilot leaked the private channel: {copilot!r}"
    )


# ─── Scenario B: Member always sees own messages ─────────────────────────


@given('user "Valentina" who is a member of channel "team-1"')
def valentina_in_team_1() -> None:
    """Seeded by `_seed`."""
    assert True


@given('a message sent in "team-1" by Valentina herself')
def valentina_sent_in_team_1() -> None:
    """Seeded by `_seed`."""
    assert True


@when("Valentina requests the channel history")
def valentina_reads_history_b(actor_conn: psycopg.Connection) -> None:
    """Reuses the same step definition as Scenario A; pytest-bdd merges
    step bodies by `When`-text so this can be a no-op here."""
    # Just delegate to the same logic.
    as_actor(actor_conn, VALENTINA)
    actor_conn.commit()
    with actor_conn.cursor() as cur:
        cur.execute(
            "SELECT rw_id, rw_channel_id, rw_body FROM rw_visible_message"
        )
        actor_conn._history_rows = cur.fetchall()


@then("her message is present despite any later role changes")
def valentina_own_message_present(actor_conn: psycopg.Connection) -> None:
    history = getattr(actor_conn, "_history_rows", [])
    bodies = [row[2] for row in history]
    assert "Hola equipo, soy Valentina" in bodies, (
        f"Valentina should see her own team-1 message; got: {history!r}"
    )
