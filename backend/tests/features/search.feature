Feature: Phase 5 — search (ts_headline) + per-channel unread + bulk mark-read

  # ─── ts_headline ──────────────────────────────────────────────────────────

  Scenario: Search returns highlighted messages within a channel
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-1" and body "hola mundo desde el equipo"
    And the user has sent a message to "team" with client_ref "ref-2" and body "goodbye world"
    When the user searches for q="hola" in "team"
    Then the response status is 200
    And the search response has exactly 1 item
    And the highlight of the first item contains <mark>hola</mark>

  Scenario: Search respects RLS — non-members get zero results
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "private"
    And the user has sent a message to "private" with client_ref "ref-s" and body "secret holaplanet"
    And the user logs in with username bob and password secret
    When the user searches for q="holaplanet" in "private"
    Then the response status is 200
    And the search response has exactly 0 items

  Scenario: Search highlight uses the actor's locale (es)
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-es" and body "Los gatos son geniales"
    When the user searches for q="gatos" in "team"
    Then the response status is 200
    And the search response has exactly 1 item
    And the highlight of the first item contains <mark>gatos</mark>

  # ─── unread badges ────────────────────────────────────────────────────────

  Scenario: Unread count starts at the number of visible messages in the channel
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent 3 messages to "team"
    When the user lists channels
    Then the channel "team" has unread_count 3

  Scenario: Mark channel read clears the unread count on next list
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent 2 messages to "team"
    When the user marks the channel "team" as read
    Then the response status is 200
    And the inserted count is at least 2
    When the user lists channels
    Then the channel "team" has unread_count 0

  Scenario: Unread count only counts messages not yet marked read
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "team"
    And the user has sent a message to "team" with client_ref "ref-1" and body "first"
    And the user has sent a message to "team" with client_ref "ref-2" and body "second"
    And the user has marked the message with client_ref "ref-1" as read
    When the user lists channels
    Then the channel "team" has unread_count 1

  Scenario: Non-member sees zero unread for a channel they cannot see
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    And the user has created a group channel named "private"
    And the user has sent a message to "private" with client_ref "ref-p" and body "secret"
    And the user logs in with username bob and password secret
    When the user lists channels
    Then the response has 0 channels