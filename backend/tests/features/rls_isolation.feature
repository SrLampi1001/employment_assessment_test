Feature: Per-user RLS isolation on rw_copilot_usage and rw_refresh_token

  Per ARCHITECTURE.md §3 + issue #22, both tables must reject any
  cross-user access — even from the runtime role with no actor GUC
  set. The tests assert this directly against the testcontainer:
  Alice writes one row to each table; Bob, connecting as the same
  `rw_app_login` role, must see zero of Alice's rows.

  # ─── rw_copilot_usage isolation ────────────────────────────────────
  Scenario: Alice's copilot usage rows are invisible to Bob
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with body "seed for embedding"
    When the user asks the copilot: "qué dijo el equipo?"
    Then the response status is 200
    When the user logs in with username bob and password secret
    When the user fetches their copilot usage
    Then the response status is 200
    And the total_calls is 0

  # ─── rw_refresh_token: runtime role has no direct table access ────
  # The runtime role (`rw_app_login`) had its table privileges on
  # rw_refresh_token REVOKEd in migration 0140 — only the SECURITY
  # DEFINER functions are granted. A direct SELECT attempted as
  # `rw_app_login` against the table must fail with
  # `permission denied for table rw_refresh_token`.
  Scenario: Runtime role has no direct SELECT on rw_refresh_token
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has logged in with username bob and password secret
    When the runtime role attempts to SELECT from rw_refresh_token directly
    Then the direct SELECT is rejected with permission denied