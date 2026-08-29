"""Unit tests for the auth use cases.

Uses **in-memory fakes** for every port — no testcontainer, no DB I/O.
The goal is to prove the use-case logic in isolation; the BDD scenarios
in `tests/features/auth.feature` prove the same logic against a real
PostgreSQL.

Every test maps to one of the three human-review checks from
`docs/DECISIONS.md`:

1. JWT middleware / sub-only rule.
2. Refresh rotation + family reuse detection.
3. Password hashing — argon2id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.auth import (
    AuthError,
    Login,
    Refresh,
    RegisterUser,
    _refresh_hash,
)
from app.infrastructure import (
    Argon2idHasher,
    PyJwtService,
    RefreshTokenRecord,
)


# ─── Fakes ──────────────────────────────────────────────────────────────


@dataclass
class _FakeUserRepo:
    """In-memory UserRepository. Stores (User, password_hash) tuples."""

    users: dict[str, tuple[Any, str]] = field(default_factory=dict)

    def __call__(self, conn=None) -> "_FakeUserRepo":
        # The factory is called once per request with a conn we ignore.
        return self

    def add(self, user, pw_hash: str) -> None:
        self.users[user.rw_username] = (user, pw_hash)

    def find_by_username(self, username: str):
        return self.users.get(username)


@dataclass
class _FakeRefreshStore:
    """In-memory refresh token store.

    Mirrors the Postgres contract — `revoke_family` must atomically
    mark every non-revoked row in the family. The test asserts this
    by counting revoked vs. non-revoked rows after a reuse event.
    """

    rows: dict[str, RefreshTokenRecord] = field(default_factory=dict)
    by_family: dict[UUID, list[str]] = field(default_factory=dict)

    def __call__(self, conn=None) -> "_FakeRefreshStore":
        return self

    def insert(self, *, user_id, token_hash, family_id, expires_at):
        rid = uuid4()
        rec = RefreshTokenRecord(
            rw_id=rid,
            rw_user_id=user_id,
            rw_token_hash=token_hash,
            rw_family_id=family_id,
            rw_expires_at=expires_at,
            rw_revoked_at=None,
        )
        self.rows[token_hash] = rec
        self.by_family.setdefault(family_id, []).append(token_hash)

    def find_by_hash(self, token_hash: str):
        return self.rows.get(token_hash)

    def revoke(self, token_id):
        for k, v in self.rows.items():
            if v.rw_id == token_id and v.rw_revoked_at is None:
                self.rows[k] = RefreshTokenRecord(
                    **{**v.__dict__, "rw_revoked_at": datetime.now(timezone.utc)}
                )

    def revoke_family(self, family_id):
        # Mirror the SQL UPDATE: one pass, family-wide, idempotent.
        for k, v in list(self.rows.items()):
            if v.rw_family_id == family_id and v.rw_revoked_at is None:
                self.rows[k] = RefreshTokenRecord(
                    **{**v.__dict__, "rw_revoked_at": datetime.now(timezone.utc)}
                )

    def is_revoked(self, token_hash: str) -> bool:
        rec = self.rows.get(token_hash)
        return rec is not None and rec.rw_revoked_at is not None

    def family_revoked_count(self, family_id: UUID) -> int:
        return sum(
            1
            for k in self.by_family.get(family_id, [])
            if self.rows[k].rw_revoked_at is not None
        )


@dataclass
class _FakeSession:
    """Minimal `SessionFactory` for tests. The fakes carry their own
    state; the connection only needs to support `cursor()` for the
    `SET LOCAL app.current_user_id` call inside `RwSession.__enter__`."""

    def __call__(self):
        return _NullConnection()


class _NullCursor:
    """No-op cursor — RwSession calls execute() once, fetchone() never."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return None


class _NullConnection:
    """Duck-types a psycopg.Connection for the fake wiring."""

    def cursor(self):
        return _NullCursor()

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


# ─── Shared test objects ────────────────────────────────────────────────


def _user(username="alice", user_id=None):
    from app.domain import User

    return User(
        rw_id=user_id or uuid4(),
        rw_username=username,
        rw_display_name=username.title(),
        rw_locale="es",
        rw_created_at=datetime.now(timezone.utc),
    )


def _jwt_service() -> PyJwtService:
    return PyJwtService("test-secret-with-multiple-characters", access_ttl_seconds=900)


def _hasher() -> Argon2idHasher:
    return Argon2idHasher()


# ─── argon2id (the password hashing check) ──────────────────────────────


def test_password_hasher_uses_argon2id() -> None:
    """Per ARCHITECTURE.md §7, hashes must be argon2id."""
    h = _hasher()
    digest = h.hash("hunter2")
    assert digest.startswith("$argon2id$"), (
        f"expected argon2id hash, got prefix {digest[:14]!r}"
    )


def test_password_hasher_verifies_correct_password_and_rejects_wrong() -> None:
    h = _hasher()
    digest = h.hash("hunter2")
    assert h.verify(digest, "hunter2") is True
    assert h.verify(digest, "hunter3") is False


# ─── JwtService (the sub-only check) ───────────────────────────────────


def test_access_jwt_carries_sub_only() -> None:
    """Per AGENTS.md / Prohibited Actions: no role / membership claims.

    The JWT must carry `sub` (stringified user_id), `iat`, `exp`,
    `type`, and nothing else. No `role`, no `channel_ids`, no
    `permissions` — those would be a security hole.
    """
    import jwt as pyjwt

    svc = _jwt_service()
    uid = uuid4()
    token = svc.issue_access(uid)

    payload = pyjwt.decode(token, "test-secret-with-multiple-characters", algorithms=["HS256"])
    assert payload["sub"] == str(uid)
    assert payload["type"] == "access"
    # No role / membership / admin claims ever.
    forbidden = {"role", "roles", "channel_ids", "channels", "permissions",
                 "is_admin", "scope", "scopes", "groups", "channel"}
    assert not (forbidden & set(payload.keys())), (
        f"JWT payload leaked privileged claims: {forbidden & set(payload.keys())}"
    )


def test_decode_access_rejects_token_with_wrong_type() -> None:
    import jwt as pyjwt

    now = datetime.now(timezone.utc)
    refresh_payload = {
        "sub": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "type": "refresh",
    }
    token = pyjwt.encode(refresh_payload, "test-secret-with-multiple-characters", algorithm="HS256")
    svc = _jwt_service()
    with pytest.raises(pyjwt.InvalidTokenError, match="unexpected token type"):
        svc.decode_access(token)


def test_decode_access_rejects_expired_token() -> None:
    import jwt as pyjwt

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": str(uuid4()),
        "iat": int(past.timestamp()),
        "exp": int(past.timestamp()),
        "type": "access",
    }
    token = pyjwt.encode(payload, "test-secret-with-multiple-characters", algorithm="HS256")
    svc = _jwt_service()
    with pytest.raises(pyjwt.ExpiredSignatureError):
        svc.decode_access(token)


# ─── RegisterUser ───────────────────────────────────────────────────────


def test_register_user_rejects_invalid_locale() -> None:
    use_case = RegisterUser(_FakeSession(), _hasher())
    with pytest.raises(AuthError) as exc:
        use_case(username="bob", display_name="Bob", locale="fr", password="x")
    assert exc.value.code == "invalid-locale"


def test_register_user_rejects_long_password() -> None:
    use_case = RegisterUser(_FakeSession(), _hasher())
    with pytest.raises(AuthError) as exc:
        use_case(
            username="bob",
            display_name="Bob",
            locale="es",
            password="x" * 129,
        )
    assert exc.value.code == "invalid-password"


# ─── Login ──────────────────────────────────────────────────────────────


def test_login_returns_token_pair_when_credentials_match() -> None:
    user = _user("alice")
    repo = _FakeUserRepo()
    repo.add(user, _hasher().hash("hunter2"))
    store = _FakeRefreshStore()

    use_case = Login(
        session_factory=_FakeSession(),
        user_repo_factory=repo,
        refresh_store_factory=store,
        hasher=_hasher(),
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    pair = use_case(username="alice", password="hunter2")

    assert pair.access_token
    assert pair.refresh_token
    assert pair.refresh_expires_at > datetime.now(timezone.utc)
    # The refresh token hash is what got persisted (not the plaintext).
    assert store.rows[_refresh_hash(pair.refresh_token)].rw_user_id == user.rw_id


def test_login_rejects_wrong_password_without_leaking_username() -> None:
    repo = _FakeUserRepo()
    repo.add(_user("alice"), _hasher().hash("hunter2"))
    store = _FakeRefreshStore()

    use_case = Login(
        session_factory=_FakeSession(),
        user_repo_factory=repo,
        refresh_store_factory=store,
        hasher=_hasher(),
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    with pytest.raises(AuthError) as exc:
        use_case(username="alice", password="WRONG")
    # Single code for both branches — no enumeration.
    assert exc.value.code == "invalid-credentials"


def test_login_rejects_unknown_username_without_leaking_existence() -> None:
    repo = _FakeUserRepo()  # empty
    store = _FakeRefreshStore()

    use_case = Login(
        session_factory=_FakeSession(),
        user_repo_factory=repo,
        refresh_store_factory=store,
        hasher=_hasher(),
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    with pytest.raises(AuthError) as exc:
        use_case(username="ghost", password="whatever")
    assert exc.value.code == "invalid-credentials"


# ─── Refresh — family reuse detection (the family-revoke check) ───────


def _login_then_get_pair(repo, store):
    return Login(
        session_factory=_FakeSession(),
        user_repo_factory=repo,
        refresh_store_factory=store,
        hasher=_hasher(),
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )


def test_refresh_rotates_under_same_family_id() -> None:
    user = _user("alice")
    repo = _FakeUserRepo()
    repo.add(user, _hasher().hash("hunter2"))
    store = _FakeRefreshStore()
    login = _login_then_get_pair(repo, store)

    first = login(username="alice", password="hunter2")

    refresh = Refresh(
        session_factory=_FakeSession(),
        refresh_store_factory=store,
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    second = refresh(refresh_token=first.refresh_token)

    # Same family — the original family_id is propagated.
    first_hash = _refresh_hash(first.refresh_token)
    second_hash = _refresh_hash(second.refresh_token)
    first_rec = store.rows[first_hash]
    second_rec = store.rows[second_hash]
    assert first_rec.rw_family_id == second_rec.rw_family_id
    # Old refresh is revoked.
    assert store.is_revoked(first_hash), (
        "old refresh token must be revoked after rotation"
    )
    # New refresh is fresh.
    assert second_rec.rw_revoked_at is None


def test_reuse_detection_revokes_entire_family() -> None:
    """The critical security test from DECISIONS.md.

    Replaying a revoked refresh token must revoke the WHOLE family
    (not just the row) so the attacker cannot keep rotating even
    after their stolen token was already exchanged.
    """
    user = _user("alice")
    repo = _FakeUserRepo()
    repo.add(user, _hasher().hash("hunter2"))
    store = _FakeRefreshStore()
    login = _login_then_get_pair(repo, store)

    first = login(username="alice", password="hunter2")

    refresh = Refresh(
        session_factory=_FakeSession(),
        refresh_store_factory=store,
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    # Legitimate rotation.
    second = refresh(refresh_token=first.refresh_token)
    # Attacker / replay: present the already-revoked first token.
    with pytest.raises(AuthError) as exc:
        refresh(refresh_token=first.refresh_token)
    assert exc.value.code == "refresh-token-revoked"

    # The whole family is now revoked — INCLUDING the second (legitimate)
    # token that the actor themselves just got. This is the cost of
    # reuse detection: it locks out everyone in the family, which is
    # what forces the user to re-login. The trade-off is deliberate.
    family_id = store.rows[_refresh_hash(first.refresh_token)].rw_family_id
    assert store.family_revoked_count(family_id) == 2, (
        "family-wide revoke must touch every row, not just the replayed one"
    )
    assert store.is_revoked(_refresh_hash(second.refresh_token))


def test_refresh_rejects_unknown_token() -> None:
    store = _FakeRefreshStore()
    refresh = Refresh(
        session_factory=_FakeSession(),
        refresh_store_factory=store,
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    with pytest.raises(AuthError) as exc:
        refresh(refresh_token="totally-bogus")
    assert exc.value.code == "invalid-refresh-token"


def test_refresh_rejects_expired_token() -> None:
    repo = _FakeUserRepo()
    user = _user("alice")
    repo.add(user, _hasher().hash("hunter2"))
    store = _FakeRefreshStore()
    refresh = Refresh(
        session_factory=_FakeSession(),
        refresh_store_factory=store,
        jwt_service=_jwt_service(),
        refresh_ttl_seconds=3600,
    )
    # Insert a refresh token that's already expired.
    family_id = uuid4()
    plaintext = "plaintext-of-expired"
    expired_hash = _refresh_hash(plaintext)
    store.insert(
        user_id=user.rw_id,
        token_hash=expired_hash,
        family_id=family_id,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    with pytest.raises(AuthError) as exc:
        refresh(refresh_token=plaintext)
    assert exc.value.code == "refresh-token-expired"
