# Dev runtime for the Riwi Co. backend. The full dependency set (main +
# dev groups) is synced into a `.venv` with `uv`; psycopg, argon2-cffi
# and uvicorn are all required at runtime, and `scripts/seed.py`
# additionally needs argon2-cffi (both live in the default+dev groups).
#
# This is a dev-stack image — it bundles everything `docker compose up`
# needs so the whole platform boots with no manual steps (per
# ARCHITECTURE.md §11).

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cached layer — only re-runs when the
# lockfile / pyproject changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Copy the application source. `--no-install-project` above means the
# workspace project itself isn't installed into .venv; we run it from
# the working dir instead (WORKDIR /app makes `app.*` importable).
COPY app ./app
COPY dev_app.py ./dev_app.py
COPY scripts ./scripts

# ── Runtime stage ────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

EXPOSE 8000

# Bind to 0.0.0.0 so the container port is reachable from the host via
# the compose mapping. `--reload` gives hot-reload in the dev stack.
CMD ["uvicorn", "dev_app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
