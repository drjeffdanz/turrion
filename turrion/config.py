"""Settings loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://turrion:turrion@localhost:5432/turrion"
    redis_url: str = "redis://localhost:6379/0"

    causal_entity_window_seconds: int = 900
    temporal_window_seconds: int = 600
    divergence_window_seconds: int = 1800

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # Deploy knobs
    seed_on_start: bool = False        # seed the freight scenario on first boot
    allowed_origins: str = "*"          # CORS: comma-separated origins, or *


settings = Settings()
