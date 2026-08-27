"""FastAPI app factory.

Wiring lives here so `app.config` is the only place that reads the
environment. Tests build their own app via
`create_app(settings=..., session_factory=...)` — no global state.

Per AGENTS.md / Prohibited Actions, no `user_id` is ever taken from
a request body — the JWT middleware is the only source of identity.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from .auth import Login, Refresh, RegisterUser
from .channels import AddMember, CreateChannel, LeaveChannel, ListVisibleChannels
from .config import Settings
from .delivery import (
    JwtAuthMiddleware,
    build_auth_router,
    build_channels_router,
    build_me_router,
)
from .infrastructure import (
    Argon2idHasher,
    PostgresChannelMemberRepository,
    PostgresChannelRepository,
    PostgresRefreshTokenStore,
    PostgresUserRepository,
    PyJwtService,
    make_session_factory,
)


def create_app(
    settings: Settings,
    *,
    session_factory: Any | None = None,
) -> FastAPI:
    """Construct the FastAPI app.

    Args:
        settings: configuration (JWT secret + TTLs + DB URL).
        session_factory: optional override for the connection factory.
            When None, a fresh-connection factory is built from
            `settings.database_url`. Tests pass a testcontainer-backed
            factory.
    """
    app = FastAPI(title="Riwi Co. Messaging Platform")

    jwt_service = PyJwtService(
        settings.jwt_secret,
        settings.access_ttl_seconds,
    )
    hasher = Argon2idHasher()

    # The middleware extracts the JWT and stashes actor_id on
    # request.state. Routes that need an actor use
    # Depends(get_current_actor) to enforce 401.
    app.add_middleware(JwtAuthMiddleware, jwt_service=jwt_service)

    factory = session_factory or make_session_factory(settings.database_url)

    # ── Phase 2 use cases (auth) ────────────────────────────────────────
    register_user = RegisterUser(factory, hasher)
    login = Login(
        session_factory=factory,
        user_repo_factory=PostgresUserRepository,
        refresh_store_factory=PostgresRefreshTokenStore,
        hasher=hasher,
        jwt_service=jwt_service,
        refresh_ttl_seconds=settings.refresh_ttl_seconds,
    )
    refresh = Refresh(
        session_factory=factory,
        refresh_store_factory=PostgresRefreshTokenStore,
        jwt_service=jwt_service,
        refresh_ttl_seconds=settings.refresh_ttl_seconds,
    )

    # ── Phase 3 use cases (channels) ────────────────────────────────────
    create_channel = CreateChannel(
        session_factory=factory,
        channel_repo_factory=PostgresChannelRepository,
        channel_member_repo_factory=PostgresChannelMemberRepository,
        user_repo_factory=PostgresUserRepository,
    )
    add_member = AddMember(
        session_factory=factory,
        channel_repo_factory=PostgresChannelRepository,
        channel_member_repo_factory=PostgresChannelMemberRepository,
    )
    list_visible = ListVisibleChannels(
        session_factory=factory,
        channel_repo_factory=PostgresChannelRepository,
    )
    leave_channel = LeaveChannel(
        session_factory=factory,
        channel_repo_factory=PostgresChannelRepository,
        channel_member_repo_factory=PostgresChannelMemberRepository,
    )

    # ── Routers ─────────────────────────────────────────────────────────
    app.include_router(
        build_auth_router(
            register_user=register_user,
            login=login,
            refresh=refresh,
        )
    )
    app.include_router(build_me_router())
    app.include_router(
        build_channels_router(
            create_channel=create_channel,
            add_member=add_member,
            list_visible=list_visible,
            leave_channel=leave_channel,
            user_repo_factory=PostgresUserRepository,
            session_factory=factory,
        )
    )

    return app
