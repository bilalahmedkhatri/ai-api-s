"""SQLAlchemy ORM models for the AI gateway."""

import datetime
import json

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
