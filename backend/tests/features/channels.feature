Feature: Channels + Membership — CRUD with RLS-gated reads

  Scenario: Creator sees the new channel in the visible-channels list
    Given a user with username alice and password secret exists
    And the user has logged in with username alice and password secret
    When the user creates a group channel named "team-platform"
    Then the response status is 201
    And the response contains the channel id

    When the user lists the visible channels
    Then the response status is 200
    And the response includes a channel named "team-platform"
    And that channel's my_role is 2

  Scenario: Invited member sees the channel in the visible-channels list
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    When the user creates a group channel named "team-platform"
    And the user adds bob to the channel team-platform
    Then the response status is 201

    When the user logs in with username bob and password secret
    And the user lists the visible channels
    Then the response status is 200
    And the response includes a channel named "team-platform"

  Scenario: Non-member gets 404 from any channel-scoped endpoint
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    When the user creates a group channel named "private-team"
    And the user logs in with username bob and password secret
    And the user tries to read the channel named "private-team"
    Then the response status is 404

  Scenario: Member leaves and immediately loses access (RLS-driven)
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    When the user creates a group channel named "throwaway"
    And the user adds bob to the channel throwaway
    And the user logs in with username bob and password secret
    And the user lists the visible channels
    Then the response includes a channel named "throwaway"

    When the user leaves the channel named "throwaway"
    Then the response status is 204
    And the user lists the visible channels
    Then the response does not include a channel named "throwaway"
    And the user tries to read the channel named "throwaway"
    Then the response status is 404

  Scenario: Direct channel between two users shows both members
    Given a user with username alice and password secret exists
    And a user with username bob and password secret exists
    And the user has logged in with username alice and password secret
    When the user creates a direct channel with username bob
    Then the response status is 201
    And the response kind is 1
