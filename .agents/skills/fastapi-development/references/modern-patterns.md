# Modern FastAPI Patterns

These are patterns that have been the documented, idiomatic way to write FastAPI code for a while now (roughly the Pydantic v2 era onward) and are unlikely to change again soon. They're a solid default even without a fresh search, but for anything version-specific, Steps 1–2 in SKILL.md still take priority.

## Table of contents
- Dependency injection with `Annotated`
- Lifespan-managed resources
- Pydantic v2 models and settings
- Project structure for bigger apps
- `async def` vs `def`
- Response models (separate input/output schemas)
- Grouped parameter models (Query/Header/Cookie)
- Background tasks
- Streaming responses (SSE, JSON Lines)
- Testing
- Security (OAuth2 + JWT sketch)

---

## Dependency injection with `Annotated`

The current recommended style wraps dependencies, path/query/body metadata in `Annotated` rather than using them as default values directly. It's not just stylistic — it lets you reuse the annotated type alias, and it plays better with tools that inspect type hints (editors, other decorators, dataclasses).

```python
from typing import Annotated
from fastapi import Depends, FastAPI, Query

app = FastAPI()

def get_query_param(q: str | None = None) -> str | None:
    return q

CommonQuery = Annotated[str | None, Query(max_length=50)]

@app.get("/items/")
async def read_items(q: CommonQuery = None, dep: Annotated[str | None, Depends(get_query_param)] = None):
    return {"q": q, "dep": dep}
```

The older style (`q: str = Query(default=None, max_length=50)`) still works and isn't deprecated outright, but `Annotated` is what current docs and examples lead with, and it's what you should default to for new code.

## Lifespan-managed resources

Application-scoped resources (DB connection pools, ML models, HTTP clients) belong in a `lifespan` async context manager, not in decorated startup/shutdown event handlers. Code before `yield` runs at startup; code after `yield` runs at shutdown, including on graceful termination signals — which the old decorator-based events didn't handle as cleanly, especially with multiple handlers.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_db_pool()
    yield
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)
```

Access shared resources from request handlers via `request.app.state` or by injecting them through a dependency, not through module-level globals.

## Pydantic v2 models and settings

Use `model_config = ConfigDict(...)` instead of a nested `class Config:`. Use `pydantic-settings`' `BaseSettings` for environment-driven config.

```python
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

class Item(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # replaces orm_mode
    name: str
    price: float

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str
    debug: bool = False
```

Common v1→v2 method renames worth knowing (see `deprecated-patterns.md` for the full table): `.dict()` → `.model_dump()`, `.json()` → `.model_dump_json()`, `.parse_obj()` → `.model_validate()`, `.parse_raw()` → `.model_validate_json()`, `.from_orm()` → `.model_validate(obj)` with `from_attributes=True` in config.

## Project structure for bigger apps

Split routers by domain/resource using `APIRouter`, and assemble them in the app entrypoint rather than defining every route on the single `FastAPI()` instance.

```
app/
├── main.py            # creates FastAPI(), includes routers, lifespan
├── dependencies.py     # shared Depends() callables
├── routers/
│   ├── users.py        # APIRouter(prefix="/users", tags=["users"])
│   └── items.py
├── models/              # Pydantic schemas
└── db/
```

```python
# routers/items.py
from fastapi import APIRouter

router = APIRouter(prefix="/items", tags=["items"])

@router.get("/")
async def list_items():
    ...

# main.py
from fastapi import FastAPI
from .routers import items, users

app = FastAPI(lifespan=lifespan)
app.include_router(items.router)
app.include_router(users.router)
```

## `async def` vs `def`

Use `async def` for path operations that do their I/O with an async-native library (e.g. `httpx.AsyncClient`, an async DB driver). Use plain `def` for handlers that call blocking/sync code (a sync DB driver, CPU-bound work, a blocking third-party SDK) — FastAPI runs those in a thread pool automatically, so you don't need to wrap them yourself. Mixing a blocking call inside an `async def` handler (e.g. calling a sync `requests.get()`) blocks the event loop for every concurrent request, which is a common and hard-to-notice performance bug.

## Response models (separate input/output schemas)

Prefer distinct Pydantic models for what a client sends versus what the API returns, rather than reusing one model for both — this keeps write-only fields (like a raw password) out of responses and lets you evolve input validation independently from output shape.

```python
class UserIn(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    username: str

@app.post("/users/", response_model=UserOut)
async def create_user(user: UserIn) -> UserOut:
    ...
```

## Grouped parameter models (Query/Header/Cookie)

Instead of listing many individual query/header/cookie parameters in a function signature, you can group related ones into a Pydantic model — this is a genuinely newer capability (not just style), so flag it as worth checking if the installed version predates it.

```python
from pydantic import BaseModel

class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")  # rejects unexpected extra query params
    page: int = 1
    size: int = 20

@app.get("/items/")
async def list_items(pagination: Annotated[Pagination, Query()]):
    ...
```

## Background tasks

For short, fire-and-forget work after returning a response (sending a confirmation email, writing a log line), use `BackgroundTasks` rather than spawning your own thread/task — it's wired into the request lifecycle so it runs after the response is sent but before the connection is fully closed.

```python
from fastapi import BackgroundTasks

@app.post("/notify/")
async def notify(email: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(send_email, email)
    return {"status": "queued"}
```

For anything long-running, retryable, or that must survive a process restart, use a real task queue (Celery, arq, etc.) instead — `BackgroundTasks` isn't durable.

## Streaming responses (SSE, JSON Lines)

FastAPI has grown native support for Server-Sent Events and streaming JSON Lines/binary data via generator functions — check whether the installed version has this before reaching for a hand-rolled `StreamingResponse` with manual formatting, since the built-in helpers handle the wire format correctly (line splitting, event framing) for you.

```python
from fastapi.responses import StreamingResponse

@app.get("/stream")
async def stream():
    async def event_generator():
        for i in range(10):
            yield f"data: {i}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

If the installed version ships a dedicated `fastapi.sse` module or similar, prefer that over hand-formatting SSE frames yourself — this is exactly the kind of thing worth confirming via Step 1/2 rather than assuming either way.

## Testing

Use `TestClient` (built on `httpx`) with plain `pytest` functions, and override dependencies rather than monkeypatching internals.

```python
from fastapi.testclient import TestClient
from .main import app, get_db

def override_get_db():
    yield FakeDB()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def test_read_item():
    response = client.get("/items/1")
    assert response.status_code == 200
```

For testing lifespan startup/shutdown behavior itself, use `TestClient` as a context manager (`with TestClient(app) as client:`) so the lifespan actually runs.

## Security (OAuth2 + JWT sketch)

FastAPI's `fastapi.security` module provides the OAuth2 scheme classes; pair with a JWT library (e.g. `pyjwt`) for token creation/verification. The framework doesn't hash passwords or issue tokens for you — that logic is yours, following the pattern in the official security tutorial.

```python
from fastapi.security import OAuth2PasswordBearer
from fastapi import Depends, HTTPException, status

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    user = decode_and_validate(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user
```