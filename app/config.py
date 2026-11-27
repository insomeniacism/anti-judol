"""Application configuration loaded from environment variables / .env."""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for anti-judol-hunter."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database (Supabase PostgreSQL)
    database_url: str = Field(
        default="",
        description="PostgreSQL connection URI for Supabase",
    )

    # OpenAI-Compatible LLM
    llm_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        description="OpenAI-compatible API base URL",
    )
    llm_api_key: str = Field(
        default="",
        description="API key for the OpenAI-compatible LLM provider",
    )
    llm_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Model identifier",
    )

    # Optional OSINT API keys
    urlscan_api_key: str = Field(
        default="",
        description="Optional URLScan.io API key for higher rate limits",
    )

    # Worker & Pipeline tuning
    worker_id: str = Field(default="local-pc")
    verify_batch_size: int = Field(default=30, ge=1, le=500)
    verify_concurrency: int = Field(default=10, ge=1, le=50)
    http_timeout_seconds: float = Field(default=12.0, ge=2.0, le=60.0)
    ai_confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    lock_timeout_minutes: int = Field(default=15, ge=1, le=120)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
