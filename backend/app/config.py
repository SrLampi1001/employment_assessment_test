"""Application configuration.

See ARCHITECTURE.md §7 (Auth) + §12 (stack). The JWT secret is the
one secret that absolutely must not be checked into git — `.env`
holds the dev value, `.env.example` holds placeholders, CI injects its own.
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
                "postgresql://rw_app_login:test_app_password@localhost:5433/db_santiago_sanchez_nakamoto",
            ),
        )
