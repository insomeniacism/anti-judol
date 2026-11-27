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
        description="Legacy single provider key (mis. DeepSeek panitia); ikut digilir bila diisi",
    )
    llm_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Model identifier",
    )

    # Multi-provider router (konsep 9router): isi key provider mana saja yang kamu punya.
    # Semua yang terisi akan digilir round-robin + cooldown otomatis saat kena 429.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    cerebras_api_key: str = ""
    cerebras_model: str = "gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    ghmodels_api_key: str = ""  # GitHub Models: PAT (scope models:read) atau GITHUB_TOKEN di Actions
    ghmodels_model: str = "openai/gpt-4.1-mini"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"
    zhipu_api_key: str = ""
    zhipu_model: str = "glm-4-flash"
    nvidia_api_key: str = ""
    nvidia_model: str = "nvidia/nemotron-3.5-bolt-e4b"
    llm_providers: str = Field(
        default="",
        description='Optional JSON array: [{"name","base_url","api_key","model","rpm"}]',
    )
    llm_cooldown_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    llm_max_wait_seconds: float = Field(default=45.0, ge=0.0, le=600.0)
    keyword_prefilter: bool = Field(
        default=True,
        description="Putus kasus judol yang sangat jelas tanpa memanggil LLM",
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
