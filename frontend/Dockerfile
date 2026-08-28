# Dev runtime for the Riwi Co. frontend — runs the Vite dev server so the
# dev stack lives entirely inside docker compose (per ARCHITECTURE.md §11).
# The dev server binds to 0.0.0.0 and serves on the port injected via the
# FRONTEND_PORT env var (no hardcoded ports, per docs/DECISIONS.md).

FROM node:22-bookworm-slim

ENV NODE_ENV=development \
    FRONTEND_PORT=5173

WORKDIR /app

# Install dependencies first (cached layer).
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# Vite CLI flags override the port in vite.config.ts, so the host mapping
# (localhost:FRONTEND_PORT) and the in-container listener stay in sync.
CMD ["sh", "-c", "npm run dev -- --host 0.0.0.0 --port $FRONTEND_PORT"]
