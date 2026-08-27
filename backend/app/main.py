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
from .copilot import AskCopilot
from .delivery import (
    JwtAuthMiddleware,
    build_auth_router,
    build_channels_router,
    build_copilot_router,
    build_me_router,
    build_messages_router,
)
from .infrastructure import (
    Argon2idHasher,
    MistralAdapter,
    NvidiaAdapter,
    PostgresChannelMemberRepository,
    PostgresChannelRepository,
    PostgresCopilotUsageRepository,
    PostgresMessageRepository,
    PostgresRefreshTokenStore,
    PostgresSearchRepository,
    PostgresUserRepository,
    ProviderError,
    PyJwtService,
    make_session_factory,
)
from .messages import (
    ChannelHistory,
    DeleteMessage,
    EditMessage,
    MarkChannelRead,
    MarkRead,
    SearchMessages,
    SendMessage,
)

from fastapi.middleware.cors import CORSMiddleware


def create_app(
    settings: Settings,
    *,
    session_factory: Any | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Construct the FastAPI app.

    Args:
        settings: configuration (JWT secret + TTLs + DB URL).
        session_factory: optional override for the connection factory.
            When None, a fresh-connection factory is built from
            `settings.database_url`. Tests pass a testcontainer-backed
            factory.
        cors_origins: list of allowed CORS origins. Defaults to
            `http://localhost:5173` (Vite dev server) so the
            frontend can hit the API during local dev. In Phase 7
            the production deployment sets the actual frontend
            origin(s).
    """
    app = FastAPI(title="Riwi Co. Messaging Platform")

    # CORS — the Vite dev server lives on a different origin. Phase 7
    # locks this down to the production frontend origin.
    origins = cors_origins or [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Idempotent-Replay"],
    )

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

    # ── Phase 4 use cases (messages) ────────────────────────────────────
    message_repo_factory = PostgresMessageRepository
    send_message_uc = SendMessage(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
    )
    edit_message_uc = EditMessage(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
    )
    delete_message_uc = DeleteMessage(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
    )
    channel_history_uc = ChannelHistory(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
    )
    mark_read_uc = MarkRead(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
    )

    # ── Phase 5 use cases (search + bulk mark-read) ─────────────────────
    search_repo_factory = PostgresSearchRepository
    search_messages_uc = SearchMessages(
        session_factory=factory,
        search_repo_factory=search_repo_factory,
    )
    mark_channel_read_uc = MarkChannelRead(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
    )

    # ── Phase 6: AI Copilot ─────────────────────────────────────────────
    # The adapters are constructed lazily — if the API key is missing,
    # we raise ProviderError on the FIRST copilot call (so the rest of
    # the app boots cleanly for dev / tests). The use case converts
    # ProviderError → CopilotError("provider-unavailable") → HTTP 503.
    def _build_embedder() -> MistralAdapter:
        if not settings.mistral_api_key:
            raise ProviderError("MISTRAL_API_KEY is not set in .env")
        return MistralAdapter(
            api_key=settings.mistral_api_key,
            model=settings.mistral_embed_model,
        )

    def _build_chatter() -> NvidiaAdapter:
        if not settings.nvidia_api_key:
            raise ProviderError("NVIDIA_API_KEY is not set in .env")
        return NvidiaAdapter(
            api_key=settings.nvidia_api_key,
            default_model=settings.chat_model_primary,
            timeout_s=settings.chat_request_timeout_s,
        )

    # For dev / test: prefer real adapters when keys are present,
    # otherwise build thin stubs that raise `ai-not-configured`. The
    # use case's `_chat_with_fallback` translates ProviderError →
    # CopilotError("ai-not-configured", ...). This keeps the rest of
    # the app + tests booting without keys, while production
    # behaviour is honoured when both are set.
    try:
        embedder = _build_embedder()
    except ProviderError:
        embedder = _UnconfiguredEmbeddingProvider()
    try:
        chatter = _build_chatter()
    except ProviderError:
        chatter = _UnconfiguredChatProvider()

    ask_copilot_uc = AskCopilot(
        session_factory=factory,
        message_repo_factory=message_repo_factory,
        usage_repo_factory=PostgresCopilotUsageRepository,
        embedder=embedder,
        chatter=chatter,
        settings=settings,
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
    app.include_router(
        build_messages_router(
            send_message=send_message_uc,
            edit_message=edit_message_uc,
            delete_message=delete_message_uc,
            channel_history=channel_history_uc,
            mark_read=mark_read_uc,
            mark_channel_read=mark_channel_read_uc,
            search_messages=search_messages_uc,
            session_factory=factory,
            message_repo_factory=message_repo_factory,
            search_repo_factory=search_repo_factory,
        )
    )
    app.include_router(
        build_copilot_router(
            ask_copilot=ask_copilot_uc,
            session_factory=factory,
        )
    )

    return app


# ─── Phase 6: dev-stub providers (raised when keys are absent) ──────


class _UnconfiguredEmbeddingProvider:
    """Stub that mirrors the EmbeddingProvider Protocol — raises
    `ProviderError("ai-not-configured")` on the first call so the
    HTTP layer can return 503 cleanly when MISTRAL_API_KEY is empty.

    Used in dev / tests when no real key is set, so the rest of the
    app boots normally.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError(
            "ai-not-configured",
            "MISTRAL_API_KEY is not set in .env",
        )


class _UnconfiguredChatProvider:
    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> tuple[str, "ChatUsage"]:
        raise ProviderError(
            "ai-not-configured",
            "NVIDIA_API_KEY is not set in .env",
        )
