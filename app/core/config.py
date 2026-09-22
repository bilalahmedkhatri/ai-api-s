"""Core application configuration using Pydantic v2 BaseSettings."""

import os

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Gateway"
    debug: bool = False
    max_results: int = 10

    # Database URL read directly from environment / .env file
    database_url: str = Field(default="")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_db_url(cls, v: str | None) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            v = os.environ.get("DATABASE_URL", "")

        v = v.strip().strip("'\"")
        if not v:
            raise ValueError(
                "DATABASE_URL environment variable is missing or empty. Please specify a valid DATABASE_URL in your environment or .env file."
            )

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
    gemini_api_key: str | None = os.environ.get("GEMINI_API_KEY")

    # Media API keys
    pexels_api_key: str | None = os.environ.get("PEXELS_API_KEY")
    pixabay_api_key: str | None = os.environ.get("PIXABAY_API_KEY")

    # Backblaze B2 settings
    b2_bucket_name: str | None = os.environ.get("B2_BUCKET_NAME")
    b2_application_key_id: str | None = os.environ.get("B2_APPLICATION_KEY_ID")
    b2_application_key: str | None = os.environ.get("B2_APPLICATION_KEY")
    b2_endpoint_url: str | None = os.environ.get("B2_ENDPOINT_URL")

    # Cron security token — set CRON_SECRET in .env to secure trigger endpoints
    cron_secret: str | None = os.environ.get("CRON_SECRET")

    # API Key requirement toggle for gateway query endpoints (default False)
    require_api_key_for_query: bool = os.environ.get("REQUIRE_API_KEY_FOR_QUERY", "false").lower() in ("true", "1", "yes")

    # Kokoro Audio TTS settings
    kokoro_model_path: str = os.environ.get("KOKORO_MODEL_PATH", "kokoro-v1_0.onnx")
    kokoro_voices_path: str = os.environ.get("KOKORO_VOICES_PATH", "voices-v1_0.bin")
    kokoro_default_voice: str = os.environ.get("KOKORO_DEFAULT_VOICE", "af_sarah")

    # Google OAuth2 — create credentials at console.cloud.google.com
    google_client_id: str | None = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret: str | None = os.environ.get("GOOGLE_CLIENT_SECRET")

    # JWT signing secret — generate with: python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str = os.environ.get("SECRET_KEY", "change-me-in-production")

    # Fernet encryption key for sensitive tokens (generate with: cryptography.fernet.Fernet.generate_key().decode())
    encryption_key: str | None = os.environ.get("ENCRYPTION_KEY")

    # Facebook API settings
    facebook_app_id: str | None = os.environ.get("FACEBOOK_APP_ID")
    facebook_app_secret: str | None = os.environ.get("FACEBOOK_APP_SECRET")

    # Frontend origin for post-OAuth redirect (must be in ALLOWED_ORIGINS too)
    frontend_origin: str = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")

    # Generated media local storage directory
    generated_media_dir: str = os.environ.get("GENERATED_MEDIA_DIR", "static/generated")


settings = Settings()
