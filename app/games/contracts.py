"""Strict request contracts for games and game comments."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GameInput(StrictInput):
    id: str = Field(min_length=1, max_length=64)
    slug: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    subtitle: str | None = None
    excerpt: str = Field(min_length=1)
    description: str = Field(min_length=1)
    cover_image: str = Field(min_length=1)
    hero_image: str | None = None
    developer: str | None = Field(default=None, max_length=128)
    publisher: str | None = Field(default=None, max_length=128)
    genres: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    release_date: date | None = None
    release_window: str | None = Field(default=None, max_length=64)
    release_status: Literal["released", "upcoming", "early_access"] = "released"
    editorial_score: float | None = Field(default=None, ge=0, le=10)
    metacritic_score: int | None = Field(default=None, ge=0, le=100)
    verdict: dict | None = None
    specs: dict | None = None
    purchase_links: dict | None = None
    publish_status: Literal["draft", "published"] = "draft"
    trending: bool = False
    is_featured: bool = False

    @field_validator("slug")
    @classmethod
    def slug_format(cls, value):
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Slug may contain letters, numbers, hyphens and underscores.")
        return value

    @model_validator(mode="after")
    def release_values(self):
        if self.release_date is not None and self.release_window is not None:
            raise ValueError("Use either release_date or release_window, not both.")
        return self


class GamePatch(StrictInput):
    slug: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    subtitle: str | None = None
    excerpt: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    cover_image: str | None = Field(default=None, min_length=1)
    hero_image: str | None = None
    developer: str | None = Field(default=None, max_length=128)
    publisher: str | None = Field(default=None, max_length=128)
    genres: list[str] | None = None
    platforms: list[str] | None = None
    release_date: date | None = None
    release_window: str | None = Field(default=None, max_length=64)
    release_status: Literal["released", "upcoming", "early_access"] | None = None
    editorial_score: float | None = Field(default=None, ge=0, le=10)
    metacritic_score: int | None = Field(default=None, ge=0, le=100)
    verdict: dict | None = None
    specs: dict | None = None
    purchase_links: dict | None = None
    publish_status: Literal["draft", "published"] | None = None
    trending: bool | None = None
    is_featured: bool | None = None

    @model_validator(mode="after")
    def nonempty_and_release_values(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field.")
        for key in (
            "slug", "title", "excerpt", "description", "cover_image", "genres",
            "platforms", "release_status", "publish_status", "trending", "is_featured",
        ):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null.")
        if self.release_date is not None and self.release_window is not None:
            raise ValueError("Use either release_date or release_window, not both.")
        return self


class CommentInput(StrictInput):
    author_name: str = Field(min_length=1, max_length=128)
    author_avatar: str | None = Field(default=None, max_length=2048)
    content: str = Field(min_length=1, max_length=5000)

    @field_validator("author_name", "content")
    @classmethod
    def trim_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Text cannot be blank.")
        return value


class ModerationInput(StrictInput):
    moderation_status: Literal["pending", "approved", "rejected"]


class GameCommentResponse(BaseModel):
    id: str
    game_id: str
    author_name: str
    author_avatar: str | None
    content: str
    upvotes: int
    downvotes: int
    moderation_status: str
    created_at: datetime


class GameResponse(BaseModel):
    id: str
    slug: str
    title: str
    subtitle: str | None
    excerpt: str
    description: str
    cover_image: str
    hero_image: str | None
    developer: str | None
    publisher: str | None
    genres: list[str]
    platforms: list[str]
    release_date: date | None
    release_window: str | None
    release_status: str
    editorial_score: float | None
    metacritic_score: int | None
    verdict: dict | None
    specs: dict | None
    purchase_links: dict | None
    publish_status: str
    trending: bool
    is_featured: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class SimilarGameResponse(BaseModel):
    id: str
    slug: str
    title: str
    excerpt: str
    cover_image: str
    hero_image: str | None
    genres: list[str]
    platforms: list[str]
    release_date: date | None
    release_status: str
    editorial_score: float | None
