Feature: Authentication — register, login, refresh, reuse detection

  Scenario: A new user can register, log in, and use the access token
    When the user registers with username bob and password supersecret
    Then the response status is 201
    And the user exists in the database

    When the user logs in with username bob and password supersecret
    Then the response status is 200
    And the response contains an access token
    And the response contains a refresh token

    When the user accesses a protected endpoint with the access token
    Then the response status is 200

  Scenario: Login with the wrong password returns 401
    Given a user with username bob2 and password supersecret exists
    When the user logs in with username bob2 and password WRONG
    Then the response status is 401

  Scenario: Login with an unknown username returns 401
    When the user logs in with username ghost and password anything
    Then the response status is 401

  Scenario: Refresh rotation issues a new pair and revokes the old refresh token
    Given a user with username carol and password supersecret exists
    And the user has logged in with username carol and password supersecret
    When the user refreshes with the valid refresh token
    Then the response status is 200
    And the response contains a new refresh token
    And the old refresh token is revoked in the database

  Scenario: Reusing a revoked refresh token revokes the entire family
    Given a user with username dave and password supersecret exists
    And the user has logged in with username dave and password supersecret
    And the user has rotated the refresh token once
    When the user attempts to refresh again with the FIRST refresh token
    Then the response status is 401
    And every refresh token in the family is revoked in the database

  Scenario: The JWT middleware rejects a request with no token
    When the user accesses a protected endpoint without an Authorization header
    Then the response status is 401

  Scenario: The JWT middleware rejects a request with an expired token
    When the user accesses a protected endpoint with an expired access token
    Then the response status is 401

  Scenario: The JWT middleware rejects a request with a tampered token
    When the user accesses a protected endpoint with a tampered token
    Then the response status is 401
