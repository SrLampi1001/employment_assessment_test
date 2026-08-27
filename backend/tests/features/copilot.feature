Feature: AI Copilot — RLS-gated retrieval + 4 refusal / inference codes

  # ─── Scenario A: non-member (deny:no-permission) ──────────────────────
  Scenario: Non-member gets deny:no-permission from the copilot (RLS gates retrieval)
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "private"
    And the user has sent a message to "private" with body "esto es un mensaje privado de alice"
    And the user logs in with username bob and password secret
    When the user asks the copilot: "qué dijo alice en el canal privado?"
    Then the response status is 200
    And the denial_code is "deny:no-permission"
    And the citations list is empty
    And the confidence is "low"
    And the response contains a prompt_version

  # ─── Scenario B: member sees their own messages ──────────────────────
  Scenario: Member sees their own messages in the copilot answer
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with body "alice habla sobre su café favorito"
    And the copilot will respond with "Aquí está la respuesta [a1b2c3]. El equipo discutió el tema."
    When the user asks the copilot: "qué dijo alice sobre el café?"
    Then the response status is 200
    And the citations list has 1 items
    And the confidence is "high"

  # ─── Scenario C: safe-comply path (the mandatory BDD scenario) ───────
  # Per issue #7 + ARCHITECTURE.md §10: deny:insufficient-context
  # transitions to infer:low-confidence on user pushback. The
  # response MUST carry the literal "Inferred with incomplete
  # context: Confidence LOW" marker.
  Scenario: Insufficient-context denial transitions to low-confidence inference on user pushback
    Given a user with username usuario and password secret exists
    And the user has logged in with username usuario and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with body "hola equipo"
    When the user asks the copilot: "detalles sobre el cierre trimestral"
    Then the response status is 200
    And the denial_code is "deny:insufficient-context"
    And the confidence is "low"
    When the user pushes back: answer anyway
    Then the response status is 200
    And the denial_code is "infer:low-confidence"
    And the answer text starts with "Inferred with incomplete context: Confidence LOW"
    And the confidence is "low"

  # ─── Audit trail (§11.4) ─────────────────────────────────────────────
  Scenario: Every copilot call creates an rw_copilot_usage row
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with body "hola equipo"
    When the user asks the copilot: "qué dijo el equipo?"
    Then the response status is 200
    When the user fetches their copilot usage
    Then the response status is 200
    And the total_calls is at least 1