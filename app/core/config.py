"""Core application configuration using Pydantic v2 BaseSettings."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
import os

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Gateway"
    debug: bool = False
    max_results: int = 10

    # Database
    database_url: str = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./gateway.db")

    # CORS — comma-separated origins in .env: ALLOWED_ORIGINS=http://localhost:3000,...
    allowed_origins: str | list[str] = Field(default=["http://localhost:3000", "http://localhost:8080"])

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # Uvicorn
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1  # aiosqlite is not fork-safe; keep 1 worker for SQLite

    # Models — override in .env
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")   # any litellm embedding model
    default_llm_model: str = os.environ.get("DEFAULT_LLM_MODEL", "gpt-4o-mini")            # fallback LLM for general queries

    # Search API keys — set in .env (leave blank to skip that engine)
    tavily_api_key: str | None = os.environ.get("TAVILY_API_KEY")
    brave_api_key: str | None = os.environ.get("BRAVE_API_KEY")

    # Upstash Redis (serverless cache) — set in .env
    upstash_redis_rest_url: str | None = os.environ.get("UPSTASH_REDIS_REST_URL")
    upstash_redis_rest_token: str | None = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

    # LLM provider API keys — set in .env; litellm reads these automatically
    # by their standard env var names (GROQ_API_KEY, etc.)
    # Listed here only for documentation; pydantic-settings will forward them.
    groq_api_key: str | None = os.environ.get("GROQ_API_KEY")
    openrouter_api_key: str | None = os.environ.get("OPENROUTER_API_KEY")
    cohere_api_key: str | None = os.environ.get("COHERE_API_KEY") 
    openai_api_key: str | None = os.environ.get("OPENAI_API_KEY") 

    # Observability
    sentry_dsn: str | None = os.environ.get("SENTRY_DSN")  # set in .env to enable Sentry error tracking


settings = Settings()
