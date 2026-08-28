"""Application configuration.

See ARCHITECTURE.md §7 (Auth) + §12 (stack). The JWT secret is the
one secret that absolutely must not be checked into git — `.env`
holds the dev value, `.env.example` holds placeholders, CI injects its own.

Phase 6: AI provider keys + chat model selection. Model names live in
config (per `ai-provider-integration` / Step 4) so a model swap is a
no-code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    access_ttl_seconds: int
    refresh_ttl_seconds: int
    database_url: str

    # Comma-separated list of allowed CORS origins (RW_CORS_ORIGINS).
    # Defaults to the Vite dev-server origins so `npm run dev` /
    # `docker compose up` work without extra config.
    cors_origins: list[str]

    # ── Phase 6: AI providers ────────────────────────────────────────
    # Empty string means "disabled" — the use case treats this as a
    # 503 with "configure MISTRAL_API_KEY in .env" (or NVIDIA_API_KEY).
    # CI does not pass them; tests use FakeEmbeddingProvider /
    # FakeChatProvider.
    mistral_api_key: str
    nvidia_api_key: str
    mistral_embed_model: str
    mistral_embed_dim: int
    chat_model_primary: str
    chat_model_fallback: str
    chat_temperature: float
    chat_request_timeout_s: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            jwt_secret=getenv(
                "RW_JWT_SECRET",
                "dev-only-jwt-secret-do-not-use-in-production",
            ),
            access_ttl_seconds=int(getenv("RW_ACCESS_TTL_SECONDS", "900")),  # 15 min
            refresh_ttl_seconds=int(
                getenv("RW_REFRESH_TTL_SECONDS", "2592000")
            ),  # 30 days
            database_url=getenv(
                "RW_DATABASE_URL",
                "postgresql://rw_app_login:dev_app_pwd@localhost:5433/db_santiago_sanchez_nakamoto",
            ),
            cors_origins=_parse_origins(
                getenv(
                    "RW_CORS_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                )
            ),
            # ── Phase 6 ──
            mistral_api_key=getenv("MISTRAL_API_KEY", ""),
            nvidia_api_key=getenv("NVIDIA_API_KEY", ""),
            mistral_embed_model=getenv(
                "MISTRAL_EMBED_MODEL", "mistral-embed"
            ),
            mistral_embed_dim=int(getenv("MISTRAL_EMBED_DIM", "1024")),
            chat_model_primary=getenv(
                "CHAT_MODEL_PRIMARY", "mistralai/mistral-nemotron"
            ),
            chat_model_fallback=getenv(
                "CHAT_MODEL_FALLBACK",
                "nvidia/nemotron-3.5-lightning-30b-a3b",
            ),
            chat_temperature=float(getenv("CHAT_TEMPERATURE", "0.2")),
            chat_request_timeout_s=float(
                getenv("CHAT_REQUEST_TIMEOUT_S", "30.0")
            ),
        )


def _parse_origins(raw: str) -> list[str]:
    """Split a comma-separated CORS origin list from the environment."""
    return [o.strip() for o in raw.split(",") if o.strip()]