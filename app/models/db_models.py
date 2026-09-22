"""SQLAlchemy ORM models for the AI gateway."""

import datetime
import json

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Query(Base):
    """Raw incoming query log."""

    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    input_type: Mapped[str] = mapped_column(String(64), nullable=False, default="text")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Flexible extra context stored as JSON text; use json.loads/dumps at the app layer.
    meta_fields: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def meta(self) -> dict:
        return json.loads(self.meta_fields) if self.meta_fields else {}

    @meta.setter
    def meta(self, value: dict) -> None:
        self.meta_fields = json.dumps(value)


class SearchResult(Base):
    """Raw response from a search aggregator (Tavily, Brave, DuckDuckGo, …)."""

    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    engine_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)  # JSON blob
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FinalResponse(Base):
    """LLM-generated final answer and associated cost metrics."""

    __tablename__ = "final_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    generated_output: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    meta_fields: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def meta(self) -> dict:
        return json.loads(self.meta_fields) if self.meta_fields else {}

    @meta.setter
    def meta(self, value: dict) -> None:
        self.meta_fields = json.dumps(value)


class AIModel(Base):
    """Registered AI models and pricing metadata."""

    __tablename__ = "ai_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    prompt_price_per_1k: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completion_price_per_1k: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ModelWebsiteUsage(Base):
    """Aggregated daily usage metrics per AI model and website origin."""

    __tablename__ = "model_website_usages"
    __table_args__ = (
        UniqueConstraint("model_id", "website_origin", "usage_date", name="uq_model_website_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False, index=True)
    website_origin: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    usage_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    total_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    model: Mapped["AIModel"] = relationship("AIModel")


class AccessAPIKey(Base):
    """Third-party application API authentication key."""

    __tablename__ = "access_apikey"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_name: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    hashed_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TTSModel(Base):
    """Dynamic TTS models configuration."""

    __tablename__ = "tts_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False) # e.g., gemini, kokoro
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Common parameters explicitly stored
    api_endpoint_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_voice: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_lang: Mapped[str | None] = mapped_column(String(32), nullable=True, default="en-us")
    default_speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # JSON field for any provider specific configs (e.g., local model paths, replicate tokens)
    provider_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def provider_config(self) -> dict:
        return json.loads(self.provider_config_json) if self.provider_config_json else {}

    @provider_config.setter
    def provider_config(self, value: dict) -> None:
        self.provider_config_json = json.dumps(value)


class TTSVoice(Base):
    """Voices linked to TTS models."""

    __tablename__ = "tts_voices"
    __table_args__ = (
        UniqueConstraint("model_id", "voice_name", name="uq_model_voice"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("tts_models.id", ondelete="CASCADE"), nullable=False, index=True)
    voice_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Check if a sample for this voice is cached locally
    is_local_cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    model: Mapped["TTSModel"] = relationship("TTSModel")


# ── User auth (Google OAuth2) ────────────────────────────────────────────────

class User(Base):
    """Authenticated user via Google OAuth2. No password stored."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    google_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    generations: Mapped[list["MediaGeneration"]] = relationship(
        "MediaGeneration", back_populates="user", cascade="all, delete-orphan"
    )


# ── Dynamic Media Generation (Image + Video) ─────────────────────────────────

class MediaModel(Base):
    """Dynamic provider configuration for image or video generation models."""

    __tablename__ = "media_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "image" | "video"
    provider: Mapped[str] = mapped_column(String(64), nullable=False)    # replicate|openai|stability|fal|generic_http
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Request construction
    api_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_endpoint_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Auth — stores the env var *name*, never the token itself
    auth_env_var: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Async polling configuration
    is_async_poll: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    poll_endpoint_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    poll_status_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    poll_success_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    poll_failed_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    poll_timeout_s: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    poll_interval_s: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Dynamic result extraction — JSON config evaluated by jsonpath_ng at runtime
    # Example: {"type": "json_path", "path": "output", "item_type": "url"}
    # Example: {"type": "json_path", "path": "artifacts[*].base64", "item_type": "base64"}
    # Example: {"type": "binary", "mime": "image/png"}
    response_extraction_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Output handling
    returns_base64: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_outputs: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    # Static extra params merged into every request body
    default_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    generations: Mapped[list["MediaGeneration"]] = relationship(
        "MediaGeneration", back_populates="model", cascade="all, delete-orphan"
    )

    @property
    def response_extraction_config(self) -> dict:
        return json.loads(self.response_extraction_config_json) if self.response_extraction_config_json else {}

    @response_extraction_config.setter
    def response_extraction_config(self, value: dict) -> None:
        self.response_extraction_config_json = json.dumps(value)

    @property
    def default_params(self) -> dict:
        return json.loads(self.default_params_json) if self.default_params_json else {}

    @default_params.setter
    def default_params(self, value: dict) -> None:
        self.default_params_json = json.dumps(value)


class MediaGeneration(Base):
    """Audit log of every image/video generation request."""

    __tablename__ = "media_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_outputs: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    extra_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_urls: Mapped[str] = mapped_column(Text, default="[]", nullable=False)  # JSON array
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)  # success|failed|timeout
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User | None"] = relationship("User", back_populates="generations")
    model: Mapped["MediaModel"] = relationship("MediaModel", back_populates="generations")


# ── Facebook Integrations ──────────────────────────────────────────────────

class FacebookPageToken(Base):
    """Encrypted Facebook Page Access Tokens provided by the user."""

    __tablename__ = "facebook_page_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "page_id", name="uq_user_page"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    page_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    page_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User | None"] = relationship("User")

class ExtractedMedia(Base):
    """Raw media extracted from external sources and stored in B2."""

    __tablename__ = "extracted_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
