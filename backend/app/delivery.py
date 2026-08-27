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
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .auth import AuthError, Login, Refresh, RegisterUser
from .channels import (
    AddMember,
    ChannelError,
    ChannelSummary,
    CreateChannel,
    LeaveChannel,
    ListVisibleChannels,
)
from .domain import (
    DIRECT,
    GROUP,
    JwtService,
    PasswordHasher,
    RefreshTokenStore,
    SessionFactory,
    User,
    UserRepository,
)
from .messages import (
    ChannelHistory,
    DeleteMessage,
    EditMessage,
    MarkChannelRead,
    MarkRead,
    MessageError,
    SearchMessages,
    SearchHitSummary,
    SendMessage,
)
from .copilot import (
    AskCopilot,
    CopilotAnswer,
    CopilotError,
)


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


# ─── Channels endpoints ─────────────────────────────────────────────────


class CreateGroupChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CreateDirectChannelIn(BaseModel):
    other_username: str = Field(min_length=1, max_length=64)


class ChannelOut(BaseModel):
    channel_id: UUID
    name: str
    kind: int
    created_by: UUID
    created_at: datetime
    my_role: int
    unread_count: int = 0


class ChannelsListOut(BaseModel):
    items: list[ChannelOut]


class AddMemberIn(BaseModel):
    new_member_id: UUID
    role: int = Field(default=1, ge=1, le=2)


class UserOut(BaseModel):
    rw_id: UUID
    rw_username: str
    rw_display_name: str
    rw_locale: str


def build_channels_router(
    *,
    create_channel: CreateChannel,
    add_member: AddMember,
    list_visible: ListVisibleChannels,
    leave_channel: LeaveChannel,
    user_repo_factory,
    session_factory,
) -> APIRouter:
    """Routes under `/api/v1/channels` + the `/api/v1/users/search`
    helper used by the invite UI.

    Authorization:
    - All routes require a JWT (Depends(get_current_actor)).
    - The `actor_id` is read from `request.state.actor_id`, never from
      the request body — the sub-only rule from `DECISIONS.md` carries
      forward from Phase 2.

    The factory `user_repo_factory` + `session_factory` are closed-over
    so the `/users/search` endpoint can open a transient RwSession
    without going through a use case (the query is read-only and has
    no business logic).
    """
    router = APIRouter(prefix="/api/v1", tags=["channels"])

    @router.post("/channels", status_code=400, include_in_schema=False)
    async def _channels_root_help() -> None:
        # Unreachable in practice — FastAPI matches more specific
        # routes first, but this stops a 405 from leaking existence.
        raise HTTPException(
            status_code=400,
            detail="use POST /channels/group or POST /channels/direct",
        )

    @router.post(
        "/channels/group", status_code=201, response_model=ChannelOut
    )
    async def create_group_channel(
        payload: CreateGroupChannelIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> ChannelOut:
        try:
            channel_id = create_channel.create_group(
                actor_id=actor, name=payload.name
            )
        except ChannelError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return _channel_summary(list_visible, actor, channel_id)

    @router.post(
        "/channels/direct", status_code=201, response_model=ChannelOut
    )
    async def create_direct_channel(
        payload: CreateDirectChannelIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> ChannelOut:
        try:
            channel_id = create_channel.create_direct(
                actor_id=actor, other_username=payload.other_username
            )
        except ChannelError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return _channel_summary(list_visible, actor, channel_id)

    @router.get("/channels", response_model=ChannelsListOut)
    async def list_visible_channels(
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> ChannelsListOut:
        items = list_visible(actor_id=actor)
        return ChannelsListOut(
            items=[
                ChannelOut(
                    channel_id=i.channel_id,
                    name=i.name,
                    kind=i.kind,
                    created_by=i.created_by,
                    created_at=i.created_at,
                    my_role=i.my_role,
                    unread_count=i.unread_count,
                )
                for i in items
            ]
        )

    @router.delete("/channels/{channel_id}", status_code=204)
    async def leave_channel_route(
        channel_id: UUID,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> None:
        try:
            leave_channel(actor_id=actor, channel_id=channel_id)
        except ChannelError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return None

    @router.post("/channels/{channel_id}/members", status_code=201)
    async def add_channel_member(
        channel_id: UUID,
        payload: AddMemberIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> dict:
        try:
            member = add_member(
                actor_id=actor,
                channel_id=channel_id,
                new_member_id=payload.new_member_id,
                role=payload.role,
            )
        except ChannelError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return {
            "channel_id": str(member.rw_channel_id),
            "user_id": str(member.rw_user_id),
            "role": member.rw_role,
        }

    @router.get("/users/search", response_model=list[UserOut])
    async def search_users(
        actor: Annotated[UUID, Depends(get_current_actor)],
        q: Annotated[str, Query(min_length=1, max_length=64)],
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> list[UserOut]:
        # Open a transient RwSession for this read. rw_user has no RLS,
        # so we don't need an actor GUC — but going through RwSession
        # keeps the connection lifecycle consistent.
        from .infrastructure import RwSession

        with RwSession(session_factory, actor_id=actor) as conn:
            repo = user_repo_factory(conn)
            users = repo.search_by_username_prefix(q, limit)
        return [
            UserOut(
                rw_id=u.rw_id,
                rw_username=u.rw_username,
                rw_display_name=u.rw_display_name,
                rw_locale=u.rw_locale,
            )
            for u in users
        ]

    return router


def _channel_summary(
    list_visible: ListVisibleChannels,
    actor: UUID,
    channel_id: UUID,
) -> ChannelOut:
    """Look up the freshly-created channel in the actor's visible list."""
    items = list_visible(actor_id=actor)
    for i in items:
        if i.channel_id == channel_id:
            return ChannelOut(
                channel_id=i.channel_id,
                name=i.name,
                kind=i.kind,
                created_by=i.created_by,
                created_at=i.created_at,
                my_role=i.my_role,
                unread_count=i.unread_count,
            )
    # If the actor can't see it, something is very wrong (they just
    # created it). Surface as 500.
    raise HTTPException(
        status_code=500, detail="created channel not visible to creator"
    )


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
    # ── Phase 3 channel codes ──────────────────────────────────────────
    "channel-not-found": status.HTTP_404_NOT_FOUND,
    "user-not-found": status.HTTP_404_NOT_FOUND,
    "invalid-kind": status.HTTP_400_BAD_REQUEST,
    "invalid-name": status.HTTP_400_BAD_REQUEST,
    "invalid-role": status.HTTP_400_BAD_REQUEST,
    "missing-other-user": status.HTTP_400_BAD_REQUEST,
    "self-direct": status.HTTP_400_BAD_REQUEST,
    "not-owner": status.HTTP_403_FORBIDDEN,
    "already-member": status.HTTP_409_CONFLICT,
    # ── Phase 4 message codes ──────────────────────────────────────────
    "invalid-body": status.HTTP_400_BAD_REQUEST,
    "invalid-client-ref": status.HTTP_400_BAD_REQUEST,
    "invalid-reason": status.HTTP_400_BAD_REQUEST,
    "invalid-limit": status.HTTP_400_BAD_REQUEST,
    "message-not-found": status.HTTP_404_NOT_FOUND,
    "not-author": status.HTTP_403_FORBIDDEN,
    "idempotent-replay": status.HTTP_200_OK,
    # ── Phase 5 search + read codes ────────────────────────────────────
    "invalid-query": status.HTTP_400_BAD_REQUEST,
    # ── Phase 6 copilot codes ──────────────────────────────────────────
    "invalid-question": status.HTTP_400_BAD_REQUEST,
    "invalid-top-k": status.HTTP_400_BAD_REQUEST,
    "embedding-unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "provider-unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "ai-not-configured": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _status_for(code: str) -> int:
    return _STATUS_MAP.get(code, status.HTTP_400_BAD_REQUEST)


# ─── Messages endpoints ─────────────────────────────────────────────────


class SendMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    client_ref: str | None = Field(default=None, min_length=1, max_length=64)


class MessageOut(BaseModel):
    rw_id: UUID
    rw_channel_id: UUID
    rw_author_id: UUID
    rw_body: str
    rw_is_edited: bool
    rw_created_at: datetime
    rw_edited_at: datetime | None
    is_mine: bool


class HistoryOut(BaseModel):
    items: list[MessageOut]
    next_cursor_created_at: datetime | None
    next_cursor_id: UUID | None


class EditMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class DeleteMessageIn(BaseModel):
    reason: str = Field(default="user-deleted", min_length=1, max_length=500)


def build_messages_router(
    *,
    send_message: SendMessage,
    edit_message: EditMessage,
    delete_message: DeleteMessage,
    channel_history: ChannelHistory,
    mark_read: MarkRead,
    mark_channel_read: MarkChannelRead,
    search_messages: SearchMessages,
    session_factory,
    message_repo_factory,
    search_repo_factory,
) -> APIRouter:
    """Routes for `/api/v1/channels/{id}/messages` + `/api/v1/messages/{id}`
    + `/api/v1/channels/{id}/search` + `/api/v1/channels/{id}/read`
    (Phase 5).

    Authorization:
    - All routes require a JWT (Depends(get_current_actor)).
    - `actor_id` comes from `request.state.actor_id` (Phase 2 sub-only rule).
    - The DB function `rw_send_message(...)` and procedures
      `rw_edit_message(...)` / `rw_delete_message(...)` re-check the
      GUC actor and channel membership as defense in depth — even
      with a valid JWT, a non-member cannot insert / edit / delete
      messages in a channel they cannot see.
    - Phase 5: `rw_search_messages` / `rw_mark_channel_read` /
      `rw_unread_count_for_channel` apply the same checks. A
      non-member gets zero results / zero unread / a no-op mark.
    """
    from .infrastructure import RwSession, PostgresMessageRepository

    router = APIRouter(prefix="/api/v1", tags=["messages"])

    @router.post(
        "/channels/{channel_id}/messages",
        status_code=201,
        response_model=MessageOut,
    )
    async def send(
        channel_id: UUID,
        payload: SendMessageIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ):
        try:
            msg, replay = send_message(
                actor_id=actor,
                channel_id=channel_id,
                body=payload.body,
                client_ref=payload.client_ref,
            )
        except MessageError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        out = MessageOut(
            rw_id=msg.rw_id,
            rw_channel_id=msg.rw_channel_id,
            rw_author_id=msg.rw_author_id,
            rw_body=msg.rw_body,
            rw_is_edited=msg.rw_is_edited,
            rw_created_at=msg.rw_created_at,
            rw_edited_at=msg.rw_edited_at,
            is_mine=msg.is_mine,
        )
        if replay:
            # Idempotent replay: return 200 (not 201) with the
            # X-Idempotent-Replay header so the frontend can detect
            # the replay without a special body field.
            response = JSONResponse(
                content=out.model_dump(mode="json"),
                status_code=200,
            )
            response.headers["X-Idempotent-Replay"] = "true"
            return response
        return out

    @router.get(
        "/channels/{channel_id}/messages", response_model=HistoryOut
    )
    async def history(
        channel_id: UUID,
        actor: Annotated[UUID, Depends(get_current_actor)],
        cursor_ts: Annotated[datetime | None, Query()] = None,
        cursor_id: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> HistoryOut:
        before = (cursor_ts, cursor_id) if (cursor_ts and cursor_id) else None
        try:
            page = channel_history(
                actor_id=actor,
                channel_id=channel_id,
                before=before,
                limit=limit,
            )
        except MessageError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return HistoryOut(
            items=[
                MessageOut(
                    rw_id=m.rw_id,
                    rw_channel_id=m.rw_channel_id,
                    rw_author_id=m.rw_author_id,
                    rw_body=m.rw_body,
                    rw_is_edited=m.rw_is_edited,
                    rw_created_at=m.rw_created_at,
                    rw_edited_at=m.rw_edited_at,
                    is_mine=m.is_mine,
                )
                for m in page.items
            ],
            next_cursor_created_at=(
                page.next_cursor[0] if page.next_cursor else None
            ),
            next_cursor_id=page.next_cursor[1] if page.next_cursor else None,
        )

    @router.patch("/messages/{message_id}", response_model=MessageOut)
    async def edit(
        message_id: UUID,
        payload: EditMessageIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> MessageOut:
        try:
            ok = edit_message(
                actor_id=actor,
                message_id=message_id,
                new_body=payload.body,
            )
        except MessageError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="message not found or not editable by this actor",
            )
        # Re-fetch the row (RLS-filtered) for the response. RLS may
        # also hide it if the actor lost membership mid-request.
        with RwSession(session_factory, actor_id=actor) as conn:
            repo = message_repo_factory(conn)
            updated = repo.find_visible(message_id, actor)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="message not visible",
            )
        return MessageOut(
            rw_id=updated.rw_id,
            rw_channel_id=updated.rw_channel_id,
            rw_author_id=updated.rw_author_id,
            rw_body=updated.rw_body,
            rw_is_edited=updated.rw_is_edited,
            rw_created_at=updated.rw_created_at,
            rw_edited_at=updated.rw_edited_at,
            is_mine=updated.rw_author_id == actor,
        )

    @router.post("/messages/{message_id}/delete", status_code=204)
    async def delete(
        message_id: UUID,
        payload: DeleteMessageIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> None:
        try:
            ok = delete_message(
                actor_id=actor,
                message_id=message_id,
                reason=payload.reason,
            )
        except MessageError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="message not found or already deleted",
            )
        return None

    @router.post("/messages/{message_id}/read", status_code=204)
    async def mark_message_read(
        message_id: UUID,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> None:
        mark_read(actor_id=actor, message_id=message_id)
        return None

    # ─── Phase 5: search + bulk mark-read ─────────────────────────────

    class SearchHitOut(BaseModel):
        rw_id: UUID
        rw_channel_id: UUID
        rw_author_id: UUID
        rw_body: str
        rw_created_at: datetime
        rw_highlight: str
        is_mine: bool

    class SearchOut(BaseModel):
        items: list[SearchHitOut]

    class MarkChannelReadOut(BaseModel):
        inserted: int

    @router.get(
        "/channels/{channel_id}/search",
        response_model=SearchOut,
    )
    async def search(
        channel_id: UUID,
        actor: Annotated[UUID, Depends(get_current_actor)],
        q: Annotated[str, Query(min_length=1, max_length=200)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> SearchOut:
        try:
            hits = search_messages(
                actor_id=actor,
                channel_id=channel_id,
                query=q,
                limit=limit,
            )
        except MessageError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return SearchOut(
            items=[
                SearchHitOut(
                    rw_id=h.rw_id,
                    rw_channel_id=h.rw_channel_id,
                    rw_author_id=h.rw_author_id,
                    rw_body=h.rw_body,
                    rw_created_at=h.rw_created_at,
                    rw_highlight=h.rw_highlight,
                    is_mine=h.is_mine,
                )
                for h in hits
            ]
        )

    @router.post(
        "/channels/{channel_id}/read",
        response_model=MarkChannelReadOut,
    )
    async def mark_channel(
        channel_id: UUID,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> MarkChannelReadOut:
        inserted = mark_channel_read(
            actor_id=actor, channel_id=channel_id
        )
        return MarkChannelReadOut(inserted=inserted)

    return router


# ─── Phase 6: AI Copilot endpoints ────────────────────────────────────


class CopilotQueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=8, ge=1, le=50)


class CitationOut(BaseModel):
    rw_id: UUID
    rw_channel_id: UUID
    snippet: str


class CopilotAnswerOut(BaseModel):
    """Wire shape for `POST /api/v1/copilot/query`.

    `denial_code` is one of the four taxonomy codes from
    `references/denial-taxonomy.md`; null when the model gave a
    normal answer. `confidence` is `"low"` for the safe-comply
    path; `"high"` otherwise. Both `200 OK` per the taxonomy.
    """

    text: str
    citations: list[CitationOut]
    denial_code: str | None
    confidence: str
    prompt_version: str


class CopilotUsageOut(BaseModel):
    """§11.4 audit aggregation per user."""

    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: float


def build_copilot_router(
    *,
    ask_copilot: AskCopilot,
    session_factory,
) -> APIRouter:
    """Routes for `/api/v1/copilot/query` + `/api/v1/copilot/usage`.

    Authorization:
    - All routes require a JWT (Depends(get_current_actor)).
    - The actor GUC is set on every RwSession inside the use case
      so RLS filters the retrieved context to the actor's visible
      channels (per ARCHITECTURE §4 — the same posture as the rest
      of the API; the LLM never sees a row the actor couldn't see
      via direct GET /messages/).
    - The `rw_copilot_usage` audit row is unconditional — success
      or failure, tokens or zero tokens (§11.4 contract).
    """
    from .infrastructure import RwSession, fetch_copilot_usage_summary

    router = APIRouter(prefix="/api/v1/copilot", tags=["copilot"])

    @router.post("/query", response_model=CopilotAnswerOut)
    async def query(
        payload: CopilotQueryIn,
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> CopilotAnswerOut:
        try:
            ans: CopilotAnswer = ask_copilot(
                actor_id=actor,
                question=payload.question,
                top_k=payload.top_k,
            )
        except CopilotError as e:
            raise HTTPException(
                status_code=_status_for(e.code), detail=e.message
            ) from None
        return CopilotAnswerOut(
            text=ans.text,
            citations=[
                CitationOut(
                    rw_id=c.rw_id,
                    rw_channel_id=c.rw_channel_id,
                    snippet=c.snippet,
                )
                for c in ans.citations
            ],
            denial_code=ans.denial_code,
            confidence=ans.confidence,
            prompt_version=ans.prompt_version,
        )

    @router.get("/usage", response_model=CopilotUsageOut)
    async def usage(
        actor: Annotated[UUID, Depends(get_current_actor)],
    ) -> CopilotUsageOut:
        with RwSession(session_factory, actor_id=actor) as conn:
            summary = fetch_copilot_usage_summary(conn, actor_id=actor)
        return CopilotUsageOut(
            total_calls=summary.total_calls,
            total_prompt_tokens=summary.total_prompt_tokens,
            total_completion_tokens=summary.total_completion_tokens,
            total_cost_usd=summary.total_cost_usd,
        )

    return router
