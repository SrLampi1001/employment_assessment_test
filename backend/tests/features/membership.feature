Feature: Visible messages by channel membership
  # ARCHITECTURE.md §10 — the two mandatory BDD scenarios as the
  # executable security spec. These scenarios are the proof that the
  # row-level security model works: they MUST pass against a real
  # `pgvector/pgvector:pg18` testcontainer running as `rw_app_login`
  # (which inherits `rw_app`, NOLOGIN, no BYPASSRLS).

  Scenario: Non-member cannot see a private channel's messages
    Given user "Valentina" who is not a member of channel "Camila-private"
    And a message sent in "Camila-private" by user "Camila"
    When Valentina requests the channel history
    And Valentina runs a messages search
    And Valentina asks the copilot
    Then the message does not appear in any of the three channels

  Scenario: A member always sees their own channel's messages
    Given user "Valentina" who is a member of channel "team-1"
    And a message sent in "team-1" by Valentina herself
    When Valentina requests the channel history
    Then her message is present despite any later role changes
