"""Strict writable input contracts. Identity, slugs and metadata are server-owned."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.blog.content import clean_text, safe_url, validate_document
from app.blog.cover_image import CoverImageInput
from app.blog.profile import description_text


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
            "status",
            "category_name",
        } & self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError("Required values cannot be null.")
        if self.category_name is not None and self.category_id is not None:
            raise ValueError("Choose a category ID or a new category name.")
        return self


class PostCreate(PostFields):
    title: str = Field(min_length=1, max_length=70)
    locale: str = "en"
    content_json: dict
    status: Literal["draft", "published"] = "draft"


class PostUpdate(PostFields):
    version: int = Field(gt=0, le=2147483647)

    @model_validator(mode="after")
    def changed_field(self):
        if self.model_fields_set <= {"version"}:
            raise ValueError("Provide a field to update.")
        return self
