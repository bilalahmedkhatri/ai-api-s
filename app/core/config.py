"""Core application configuration using Pydantic v2 BaseSettings."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
import os

load_dotenv()


from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Gateway"
    debug: bool = False
    max_results: int = 10

    # Database
    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/gateway")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_db_url(cls, v: str | None) -> str:
        default_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/gateway"
        if not v or not isinstance(v, str) or not v.strip():
            env_url = os.environ.get("DATABASE_URL")
            if env_url and env_url.strip():
                v = env_url.strip()
            else:
                return default_url

        v = v.strip().strip("'\"")
        if not v:
            return default_url

        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)

        if "?" in v:
            parsed = urlparse(v)
            query_params = parse_qs(parsed.query)

            # 1. Convert sslmode to ssl for asyncpg compatibility
            if "sslmode" in query_params:
                sslmode_val = query_params.pop("sslmode")[0]
                if sslmode_val in ("require", "verify-ca", "verify-full", "prefer", "allow"):
                    query_params["ssl"] = [sslmode_val]

            # 2. Remove libpq/MySQL parameters that asyncpg does not accept as keyword args
            unsupported_params = ["channel_binding", "target_session_attrs", "gssencmode", "krbsrvname", "charset"]
            for p in unsupported_params:
                query_params.pop(p, None)

            new_query = urlencode(query_params, doseq=True)
            v = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

        return v

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
    workers: int = 1

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
