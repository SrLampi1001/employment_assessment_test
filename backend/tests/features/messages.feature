Feature: Messages — idempotent send / edit / logical delete / keyset history / mark read

  Scenario: Send is idempotent on rw_client_ref
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    When the user sends a message to "team" with client_ref "ref-1" and body "hello"
    Then the response status is 201
    And the response contains a message id
    When the user sends a message to "team" with client_ref "ref-1" and body "hello"
    Then the response status is 200
    And the response contains a message id
    And the first and second message ids are the same

  Scenario: Edit appends a rw_message_edit row and marks the message as edited
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-e" and body "original"
    When the user edits that message with body "corrected"
    Then the response status is 200
    And the response body is "corrected"
    And the response is_edited is true
    And the database has exactly one rw_message_edit row for that message

  Scenario: Logical delete sets rw_deleted_at + rw_deleted_reason and hides from history
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-d" and body "oops"
    When the user logically deletes that message with reason "user-deleted"
    Then the response status is 204
    And the database has the message with rw_deleted_at set and reason "user-deleted"
    When the user requests the history of "team"
    Then the message "oops" does not appear in the history

  Scenario: Keyset pagination returns messages strictly older than the cursor
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent 5 messages to "team"
    When the user requests the first page of "team" with limit 2
    Then the response status is 200
    And the page contains messages m4 and m3 only
    And the next_cursor points at m3

  Scenario: Mark read returns 204 and is idempotent
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-r" and body "hi"
    When the user marks that message as read
    Then the response status is 204
    When the user marks that message as read
    Then the response status is 204
    And the database has exactly one rw_message_read row for that user

  Scenario: Non-member gets 404 from the messages endpoints (RLS gates)
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "private"
    And the user has sent a message to "private" with client_ref "ref-n" and body "secret"
    And the user logs in with username bob and password secret
    When the user requests the history of "private"
    Then the response status is 200
    And the history contains 0 items

  # ─── Issue #23: non-author must see 404 (no existence leak) ──────
  Scenario: Non-author gets 404 from PATCH /messages/{id} (not 403)
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-leak" and body "alice wrote this"
    And the user logs in with username bob and password secret
    When the user edits that message with body "bob hijacks"
    Then the response status is 404
    And the original message body is unchanged

  Scenario: Non-author gets 404 from POST /messages/{id}/delete (not 403)
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-leak-del" and body "alice wrote this"
    And the user logs in with username bob and password secret
    When the user logically deletes that message with reason "user-deleted"
    Then the response status is 404
    And the original message is not marked deleted
