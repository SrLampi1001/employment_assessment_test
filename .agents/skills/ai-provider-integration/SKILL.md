---
name: ai-provider-integration
description: Implement, review, and debug the AI copilot for the Riwi Co. Messaging Platform: Mistral `mistral-embed` (1024-dim) for embeddings and NVIDIA NIM (OpenAI-compatible) for chat via the `mistralai/mistral-nemotron` primary model with `nvidia/nemotron-3.5-lightning-30b-a3b` fallback. Trigger for any work on the AI provider layer, the versioned system prompt, the `rw_copilot_usage` audit insert, citation rendering, denial-taxonomy handling, RAG context retrieval, retry/rate-limit logic, or provider port wiring. The copilot is permission-filtered by the same PostgreSQL RLS policy as the rest of the system — there is no separate "AI security layer" — so this skill is required whenever an answer might come back from the LLM. Do NOT use for the React frontend's chat panel (separate frontend skill), for raw SQL/RLS work (use postgresql-rls-pgvector), or for the FastAPI routes themselves (use fastapi-development).
---

# AI Provider Integration — Riwi Co. Messaging Platform

> this skill is the **decision log** + **banned-pattern checklist** + **denial taxonomy**, not a copy of the implementation. If this file contradicts `/backend/app/copilot.py`, the file wins.

## Ground rule: the copilot is permission-filtered by RLS, not by the LLM

Per [`/docs/ARCHITECTURE.md §4`](../../../docs/ARCHITECTURE.md), the copilot's context window is filled with rows from `rw_visible_message` *after* the same RLS policy as the rest of the system has filtered them. **There is no separate "AI permission layer"** — the same `app.current_user_id` GUC that protects `/api/v1/messages/search` protects `/api/v1/copilot/query`. The LLM never sees rows the actor couldn't see via direct API.

Consequences:

- The provider ports return *embeddings* and *chat answers*. They do **not** fetch context — that's a repository (`MessageRepo.search_similar`).
- The system prompt instructs the model to **decline** with one of three explicit refusal codes (`deny:no-permission`, `deny:out-of-scope`, `deny:insufficient-context`) when the user asks about something the visible context doesn't contain (or doesn't have permission to discuss), and to comply with a **fourth** code (`infer:low-confidence`) when the user pushes back on an insufficient-context refusal. The full taxonomy is in `references/denial-taxonomy.md`.
- Every copilot call ends with an insert into `rw_copilot_usage` — model name, prompt tokens, completion tokens — for the §11.4 audit report.

## Where the implementation actually lives (verified 2026-08-29)

| Concern | Shipped file | One-line summary |
|---|---|---|
| Provider ports (`EmbeddingProvider`, `ChatProvider`) | `/backend/app/domain.py` | `Protocol`s in a single `domain.py` (flat layout, per ARCH §5.2). Use cases depend on these only — never on `mistralai` / `httpx`. |
| Embedding adapter | `/backend/app/infrastructure.py` (`MistralAdapter`) | Sync `Mistral` SDK; `BATCH_LIMIT = 512`, `MAX_RETRIES = 3` with exponential backoff on `429` / `5xx`. |
| Chat adapter | `/backend/app/infrastructure.py` (`NvidiaAdapter`) | Sync `httpx.Client` (not `AsyncClient`) against `https://integrate.api.nvidia.com/v1/chat/completions` (OpenAI-compatible). |
| Use case | `/backend/app/copilot.py` (`AskCopilot`) | Orchestrates embed → retrieve → chat → audit. Defines the four denial-code constants. |
| System prompt | `/backend/app/copilot_prompt.py` (`PROMPT_VERSION`, `BASE_SYSTEM_PROMPT`) | `PROMPT_VERSION = "2026-08-27.6"` — bump on every text change so the audit row can bisect. |
| Settings (model names live in config, not code) | `/backend/app/config.py` (`Settings.chat_model_primary`, `chat_model_fallback`) | `@dataclass(frozen=True)`, hand-rolled `from_env()` via `os.getenv` (NOT pydantic-settings). |
| Wiring | `/backend/app/main.py` (`create_app`) | `create_app(settings=..., session_factory=..., embedder=..., chatter=...)` — `embedder` and `chatter` are injection seams for `FakeEmbeddingProvider` / `FakeChatProvider` in tests. |

## Project baseline (per `ARCHITECTURE.md §4.3` + §12)

| Concern | Choice | Why |
|---|---|---|
| Embeddings | **Mistral `mistral-embed`**, 1024 dims (`vector(1024)` in `rw_message`) | Free "Experiment" tier; pinned by ARCHITECTURE §4.3 |
| Embedding fallback | **`nvidia/nemotron-3-embed-1b`** | If Mistral free cap is exceeded; config-only swap |
| Chat primary | **`mistralai/mistral-nemotron`** via NVIDIA NIM (`https://integrate.api.nvidia.com/v1`) | Mistral model optimized by NVIDIA; first-class Spanish support; replaces the deprecated `meta/llama-3.3-70b-instruct` (deprecation 2026-08-25) |
| Chat fallback | **`nvidia/nemotron-3.5-lightning-30b-a3b`** via the same endpoint | Faster, English-optimized; config-only swap |
| Model name source | `Settings.chat_model_primary: str`, `Settings.chat_model_fallback: str` (`@dataclass` from env) | Model name is config, not code |
| Embedding batching | Up to 512 texts per `embeddings.create(inputs=[...])` call | Mistral free-tier rate-limit friendliness — a 50k-message seed becomes ~98 calls instead of 50k |
| Retry policy | Exponential backoff on `429` / `5xx`, `MAX_RETRIES = 3`; no circuit breaker | Don't melt the free tier on a seed loop |
| Sync vs async | **Sync adapters**, sync use case | Matches the rest of the codebase (psycopg sync, sync `RwSession`); revisit if an async stack is introduced |
| Token / cost logging | Always, on every call, success or failure (record 0 tokens on failure) | `rw_copilot_usage` is the audit trail (the `record` call now goes through the `rw_record_copilot_usage` SECURITY DEFINER function, per migration 0140) |

## Step 1: System prompt — versioned, explicit denials

`PROMPT_VERSION` lives in [`/backend/app/copilot_prompt.py`](../../../backend/app/copilot_prompt.py) and is embedded verbatim in the prompt body so a `PROMPT_VERSION` bump creates a different string and a different `rw_copilot_usage.rw_model` audit value. The denial taxonomy (below) is documented in `references/denial-taxonomy.md`. The system prompt's wording must match the taxonomy exactly — the BDD tests assert on it. The four codes:

- `deny:no-permission`
- `deny:out-of-scope`
- `deny:insufficient-context`
- `infer:low-confidence` (the safe-comply path when the user pushes back on a refusal — see the Gherkin scenario in `references/denial-taxonomy.md`)

## Step 2: Fallback chain

Two layers:

1. **Embedding fallback** — `Settings.mistral_embed_model` → `nvidia/nemotron-3-embed-1b` if Mistral returns `429` for the rolling minute.
2. **Chat fallback** — primary model fails (`429` or repeated `5xx`) → switch to `Settings.chat_model_fallback` for the rest of the request. Implementation lives in the use case (`AskCopilot.__call__`), not the adapter — keeps the adapter dumb.

The fallback model name lives in `Settings`, not in the adapter. Don't hardcode `nvidia/nemotron-3.5-lightning-30b-a3b` anywhere outside `Settings`.

## Step 3: Citation rendering (where the answer meets the UI)

Retrieved messages are wrapped in `<message id=... channel_id=... created_at=...>...</message>` delimiters and labelled **UNTRUSTED** in the system prompt. The strict XML-style delimiters make prompt-injection from message bodies much harder to miss in evaluation. If the model nevertheless echoes any part of the system prompt verbatim, treat it as a regression in the prompt and open an issue. The versioned `PROMPT_VERSION` constant lets you bisect which prompt text the failure appeared in.

The `[<message_id>]` form is the only one the UI knows how to render. Don't accept prose like "see the third message" or "as mentioned above" — those are prompt-completeness bugs.

## Step 4: Testing the providers

Three test layers:

1. **Unit tests** for `AskCopilot` use a `FakeChatProvider` and a fake message repo (in-memory). No network.
2. **Adapter smoke tests** hit the real endpoint with one short prompt; gated by env var (`RUN_AI_SMOKE=1`). Skipped in CI by default; run manually before merging changes to the adapters.
   - **The agent must NEVER run these tests automatically** — they require real API keys in `.env`.
3. **BDD** (via the `pytest-bdd-testcontainers` skill) verifies end-to-end behavior against the real `pgvector` extension — RLS, keyset, citations, denials.

Real network calls to Mistral / NVIDIA NIM belong in **adapter smoke tests** under `backend/tests/infrastructure/ai/`, gated by env vars and skipped in default CI runs. Live API keys are never recorded in tests or fixtures.

## Step 5: Project-banned patterns (the copilot-specific list)

In addition to the general `AGENTS.md` prohibited actions:

| Banned | Use instead |
|---|---|
| Calling `mistralai.Mistral(...)` or `httpx.post(...)` inside a use case | Inject the provider port; use cases depend only on `EmbeddingProvider` / `ChatProvider` |
| Hardcoding `mistral-embed` or `mistralai/mistral-nemotron` in code | Read from `Settings.chat_model_primary` / `chat_model_fallback`; config-only swap |
| Embedding one message at a time (`for msg in messages: adapter.embed([msg])`) | Batch up to 512 texts per call |
| Skipping the `rw_copilot_usage` insert on failure ("nothing was generated") | Always record — model + 0 tokens on failure (goes through the `rw_record_copilot_usage` SECURITY DEFINER function, per migration 0140) |
| Returning the LLM's answer with no citations | Always require `[message_id]` citations; reject in the use case if missing |
| Using the LLM to decide whether the actor has permission | Trust the RLS policy that already filtered the context; the model never sees invisible rows |
| Building the system prompt via `f"""...{user_input}..."""` | System prompt is a constant; user input goes in the user message |
| Logging `Authorization: Bearer ...` or the full request body | Log only the model name, token counts, and actor id |
| Storing `mistral_api_key` / `nvidia_api_key` in `.env.example` with real values | `.env.example` ships placeholders only |
| Letting the model output JSON the frontend will `eval()` | Return a typed Pydantic `CopilotAnswer` (text + citations) |
| Skipping retries on `429` | Exponential backoff, max 3 attempts, then either fall back or surface `ProviderUnavailable` |
| Calling `INSERT INTO rw_copilot_usage` directly from the application | `rw_copilot_usage` has RLS enabled; the application role can only SELECT; writes go through `rw_record_copilot_usage(...)` SECURITY DEFINER (migration 0140) |

## Step 6: Where to go next

- For the RLS-filtered retrieval, use the `postgresql-rls-pgvector` skill (the `rw_visible_message` view + `embedding <=>` query).
- For the use case / repository / unit-of-work wiring, use the `fastapi-development` skill.
- For the end-to-end BDD tests (login → message → search → copilot → denial), use the `pytest-bdd-testcontainers` skill.
- For architectural questions, the source of truth is `/docs/ARCHITECTURE.md §4`. If this skill and the architecture disagree, **the architecture wins**.