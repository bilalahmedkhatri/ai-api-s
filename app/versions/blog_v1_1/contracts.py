"""Strict writable input contracts. Identity, slugs and metadata are server-owned."""

import re
from datetime import datetime
from typing import Literal

from app.blog.content import clean_text, safe_url, validate_document
from app.blog.cover_image import CoverImageInput
from app.blog.profile import description_text
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


def normalized_locale(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value
    ):
        raise ValueError("Invalid locale.")
    parts = value.split("-")
    return "-".join(
        [parts[0].lower()]
        + [
            p.title()
            if len(p) == 4 and p.isalpha()
            else p.upper()
            if len(p) == 2 and p.isalpha()
            else p.lower()
            for p in parts[1:]
        ]
    )


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImageDetails(CoverImageInput):
    asset_id: int = Field(gt=0, le=2147483647)


class BlogDetails(StrictInput):
    featured_image: ImageDetails | None = None
    content_images: list[ImageDetails] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique_images(self):
        ids = [image.asset_id for image in self.content_images]
        if len(ids) != len(set(ids)):
            raise ValueError("Content image details must have unique asset IDs.")
        return self


class CategoryInput(StrictInput):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def category_name(cls, value):
        return clean_text(value, 128)


class ProfileInput(StrictInput):
    name: str | None = Field(default=None, max_length=80)
    designation: str | None = Field(default=None, max_length=160)
    avatar_url: str | None = Field(default=None, max_length=512)
    avatar_upload_token: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def profile_values(self):
        if not self.model_fields_set:
            raise ValueError("Provide a profile field.")
        if "name" in self.model_fields_set:
            self.name = clean_text(self.name, 80)
        if self.designation is not None:
            self.designation = clean_text(self.designation, 160, empty=True) or None
        if "avatar_url" in self.model_fields_set:
            self.avatar_url = safe_url(self.avatar_url) if self.avatar_url else None
        if self.description is not None:
            self.description = description_text(self.description) or None
        return self


class PostFields(StrictInput):
    title: str | None = Field(default=None, max_length=70)
    locale: str | None = Field(default=None, max_length=35)
    excerpt: str | None = Field(default=None, max_length=1000)
    category_id: int | None = Field(default=None, gt=0, le=2147483647)
    category_name: str | None = Field(default=None, min_length=1, max_length=128)
    cover_asset_id: int | None = Field(default=None, gt=0, le=2147483647)
    cover_image: CoverImageInput | None = None
    content_json: dict | None = None
    blog_details: BlogDetails | None = None
    status: Literal["draft", "published", "archived"] | None = None

    @field_validator("title", "category_name")
    @classmethod
    def required_text(cls, value, info: ValidationInfo):
        return (
            None
            if value is None
            else clean_text(value, 128 if info.field_name == "category_name" else 70)
        )

    @field_validator("excerpt")
    @classmethod
    def optional_text(cls, value):
        return None if value is None else clean_text(value, 1000, empty=True)

    @field_validator("locale")
    @classmethod
    def locale_value(cls, value):
        return None if value is None else normalized_locale(value)

    @field_validator("content_json")
    @classmethod
    def document_value(cls, value):
        return None if value is None else validate_document(value)

    @model_validator(mode="after")
    def fields_valid(self):
        for field in {
            "title",
            "locale",
            "excerpt",
            "content_json",
            "blog_details",
            "status",
            "category_name",
        } & self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError("Required values cannot be null.")
        if self.category_name is not None and self.category_id is not None:
            raise ValueError("Choose a category ID or a new category name.")
        return self


class PostCreate(PostFields):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Example blog",
                    "excerpt": "Short summary",
                    "locale": "en",
                    "status": "draft",
                    "content_json": {"type": "doc", "content": []},
                    "blog_details": {"featured_image": None, "content_images": []},
                }
            ]
        }
    )
    title: str = Field(min_length=1, max_length=70)
    locale: str = "en"
    content_json: dict
    status: Literal["draft", "published"] = "draft"


class PostUpdate(PostFields):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "version": 3,
                    "excerpt": "Updated summary",
                    "content_json": {"type": "doc", "content": []},
                    "blog_details": {"featured_image": None, "content_images": []},
                }
            ]
        }
    )
    version: int = Field(gt=0, le=2147483647)

    @model_validator(mode="after")
    def changed_field(self):
        if self.model_fields_set <= {"version"}:
            raise ValueError("Provide a field to update.")
        return self


class ErrorResponse(BaseModel):
    """Stable error envelope used by the V1.1 OpenAPI contract."""

    detail: str


class CategoryResponse(StrictInput):
    id: int
    name: str
    normalized_name: str
    slug: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssetResponse(StrictInput):
    """Public asset fields only; storage object keys are never returned."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: int
    public_url: str
    original_filename: str
    media_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    alt_text: str = ""
    caption: str | None = None
    created_at: datetime | None = None


class PostResponse(StrictInput):
    id: int
    locale: str
    title: str
    slug: str
    excerpt: str
    category_id: int | None = None
    content_json: dict | None = None
    content_schema_version: int
    metadata_json: dict
    blog_details_json: dict = Field(default_factory=dict)
    author_name: str
    author_designation: str | None = None
    author_avatar_url: str | None = None
    author_description: str | None = None
    cover_asset_id: int | None = None
    status: Literal["draft", "published", "archived"]
    version: int
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class PostEnvelope(StrictInput):
    post: PostResponse
    audit_recorded: bool | None = None


class PostListResponse(StrictInput):
    items: list[PostResponse]
    total: int
    page: int
    page_size: int
    scope: Literal["own", "all"]
    counts: dict[str, int]


class CategoryListResponse(StrictInput):
    items: list[CategoryResponse]
    total: int
    page: int
    page_size: int


class AssetListResponse(StrictInput):
    assets: list[AssetResponse]
    total: int
    page: int
    page_size: int


class CategoryEnvelope(StrictInput):
    category: CategoryResponse
    created: bool
    audit_recorded: bool


class UploadEnvelope(StrictInput):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "asset": {
                        "id": 12,
                        "public_url": "https://media.example/blogs/app/example-uuid.png",
                        "original_filename": "example-uuid.png",
                        "media_type": "image/png",
                        "size_bytes": 12000,
                    },
                    "reused": False,
                    "audit_recorded": True,
                }
            ]
        }
    )
    asset: AssetResponse
    reused: bool
    audit_recorded: bool | None = None


class AppResponse(StrictInput):
    key: str
    label: str
    configured: bool


class AppsResponse(StrictInput):
    apps: list[AppResponse]


class SettingsResponse(StrictInput):
    key: str
    label: str
    public_blog_url: str | None = None
    publishing_configured: bool


class ProfileResponse(StrictInput):
    name: str
    designation: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    complete: bool
    missing_fields: list[str]


class AvatarUploadResponse(StrictInput):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "url": "https://media.example/blogs/authors/12/author-name-hash.png",
                    "upload_token": "signed-receipt",
                }
            ]
        }
    )
    url: str
    upload_token: str
