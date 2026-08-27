"""Delivery layer: JWT middleware + auth HTTP routes.

Per ARCHITECTURE.md §7 + AGENTS.md (Prohibited Actions), the user id
**only** comes from the verified JWT `sub`. The middleware sets
`request.state.actor_id` (a `UUID`, never a string) and the routes
that need an actor pull it from there. **There is no `user_id` in
any request body** — `Login`, `Register`, `Refresh` operate on
credentials, not on a claimed identity.

Middleware vs. route-level enforcement:
- The middleware validates the JWT if present and stashes the actor
  on the request state. It does NOT reject requests that lack a
  token — those routes are `/auth/register`, `/auth/login`, and
  `/auth/refresh`, which by definition have no actor yet.
- Routes that require authentication take a `Depends(get_current_actor)`
  parameter. The dependency raises `HTTPException(401, ...)` if
  `request.state.actor_id` is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from .auth import AuthError, Login, Refresh, RegisterUser
from .domain import JwtService, PasswordHasher, RefreshTokenStore, SessionFactory, UserRepository


# ─── Middleware ──────────────────────────────────────────────────────────


@dataclass
class _AuthState:
    """Per-request auth state attached via `request.state`."""

    actor_id: UUID | None


def _bearer_prefix(value: bytes) -> bool:
    return value.startswith(b"Bearer ")


class JwtAuthMiddleware:
    """Pure ASGI middleware. Reads `Authorization: Bearer <jwt>`,
    decodes + verifies, stashes `actor_id` on `request.state`. Never
    rejects a request — that decision belongs to the route's
    `Depends(get_current_actor)`.
    """

    def __init__(self, app: Any, jwt_service: JwtService) -> None:
        self._app = app
        self._jwt = jwt_service

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        actor_id: UUID | None = None
        for raw_key, raw_value in scope.get("headers", []):
            if raw_key == b"authorization" and _bearer_prefix(raw_value):
                token = raw_value[len(b"Bearer "):].decode("ascii", errors="replace")
                try:
                    actor_id = self._jwt.decode_access(token)
                except jwt.PyJWTError:
                    # Token was present but invalid — leave actor_id None;
                    # the route's Depends(get_current_actor) will 401.
                    actor_id = None
                break

        # Make sure scope["state"] is a mutable State object. Starlette's
        # TestClient sometimes pre-fills it with a `dict` (which doesn't
        # support attribute assignment); production leaves scope["state"]
        # absent. Replace any non-State value with a fresh State().
        from starlette.datastructures import State

        existing = scope.get("state")
        if isinstance(existing, State):
            state = existing
        else:
            state = State()
        state.actor_id = actor_id  # type: ignore[attr-defined]
        scope["state"] = state

        await self._app(scope, receive, send)


async def get_current_actor(request: Request) -> UUID:
    """FastAPI dependency. Returns the actor id from the JWT.

    Raises 401 if the JWT was missing, malformed, or expired. Routes
    that need an authenticated user declare `actor: Annotated[UUID,
    Depends(get_current_actor)]`.
    """
    actor_id: UUID | None = getattr(request.state, "actor_id", None)
    if actor_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return actor_id


# ─── Pydantic request / response schemas ────────────────────────────────


class RegisterIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    locale: str

    @field_validator("locale")
    @classmethod
    def _validate_locale(cls, v: str) -> str:
        if v not in ("es", "en"):
            raise ValueError("locale must be 'es' or 'en'")
        return v

    password: str = Field(min_length=1, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


class RegisterOut(BaseModel):
    user_id: UUID


# ─── FastAPI router ─────────────────────────────────────────────────────


def build_auth_router(
    *,
    register_user: RegisterUser,
    login: Login,
    refresh: Refresh,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    @router.post(
        "/register",
        status_code=status.HTTP_201_CREATED,
        response_model=RegisterOut,
    )
    async def register(payload: RegisterIn) -> RegisterOut:
        try:
            user_id = register_user(
                username=payload.username,
                display_name=payload.display_name,
                locale=payload.locale,
                password=payload.password,
            )
        except AuthError as e:
            raise HTTPException(
                status_code=_status_for(e.code),
                detail=e.message,
            ) from None
        return RegisterOut(user_id=user_id)

    @router.post("/login", response_model=TokenPairOut)
    async def login_route(payload: LoginIn) -> TokenPairOut:
        try:
            pair = login(username=payload.username, password=payload.password)
        except AuthError as e:
            raise HTTPException(
                status_code=_status_for(e.code),
                detail=e.message,
            ) from None
        return TokenPairOut(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            refresh_expires_at=pair.refresh_expires_at,
        )

    @router.post("/refresh", response_model=TokenPairOut)
    async def refresh_route(payload: RefreshIn) -> TokenPairOut:
        try:
            pair = refresh(refresh_token=payload.refresh_token)
        except AuthError as e:
            raise HTTPException(
                status_code=_status_for(e.code),
                detail=e.message,
            ) from None
        return TokenPairOut(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            refresh_expires_at=pair.refresh_expires_at,
        )

    return router


# ─── /me — Phase 3 will replace this with the full profile route ───────


class MeOut(BaseModel):
    actor_id: UUID


def build_me_router() -> APIRouter:
    """Minimal `GET /api/v1/me` for the Phase 2 auth middleware tests.

    Phase 3 (Profile) replaces this with the full profile endpoint;
    the route exists in Phase 2 only as a target that requires a
    valid JWT, so the middleware's behavior can be asserted in BDD.
    """
    router = APIRouter(prefix="/api/v1", tags=["me"])

    @router.get("/me", response_model=MeOut)
    async def me(actor: Annotated[UUID, Depends(get_current_actor)]) -> MeOut:
        return MeOut(actor_id=actor)

    return router


# ─── Status code mapping ────────────────────────────────────────────────


_STATUS_MAP = {
    "invalid-credentials": status.HTTP_401_UNAUTHORIZED,
    "invalid-refresh-token": status.HTTP_401_UNAUTHORIZED,
    "refresh-token-revoked": status.HTTP_401_UNAUTHORIZED,
    "refresh-token-expired": status.HTTP_401_UNAUTHORIZED,
    "invalid-username": status.HTTP_400_BAD_REQUEST,
    "invalid-display-name": status.HTTP_400_BAD_REQUEST,
    "invalid-locale": status.HTTP_400_BAD_REQUEST,
    "invalid-password": status.HTTP_400_BAD_REQUEST,
    "username-taken": status.HTTP_409_CONFLICT,
}


def _status_for(code: str) -> int:
    return _STATUS_MAP.get(code, status.HTTP_400_BAD_REQUEST)
