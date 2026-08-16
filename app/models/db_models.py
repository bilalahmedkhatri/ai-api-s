"""SQLAlchemy ORM models for the AI gateway."""

import datetime
import json

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
