"""Validated, post-scoped featured-image details in existing metadata JSON."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.blog.content import clean_text, safe_url


class ImageAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    credit: str = Field(default="", max_length=200)
    sourceUrl: str = Field(default="", max_length=2048)
    license: str = Field(default="", max_length=200)
    licenseUrl: str = Field(default="", max_length=2048)

    @field_validator("credit", "license")
    @classmethod
    def text(cls, value):
        return clean_text(value, 200, empty=True)

    @field_validator("sourceUrl", "licenseUrl")
    @classmethod
    def url(cls, value):
        return safe_url(value.strip()) if value.strip() else ""

    @model_validator(mode="after")
    def labels(self):
        if self.sourceUrl and not self.credit:
            raise ValueError("Provide a creator or source name for the source link.")
        if self.licenseUrl and not self.license:
            raise ValueError("Provide a license name for the license link.")
        return self


class CoverImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    alt_text: str = Field(default="", max_length=1000)
    attribution: ImageAttribution = Field(default_factory=ImageAttribution)

    @field_validator("alt_text")
    @classmethod
    def alt(cls, value):
        return clean_text(value, 1000, empty=True)


def apply_cover_details(values, details=...):
    metadata = dict(values.get("metadata_json") or {})
    previous = metadata.pop("cover_image", None)
    cover = values.get("cover_asset_id")
    if details is not ... and details is not None:
        if cover is None:
            raise HTTPException(422, "Select a featured image before adding its details.")
        metadata["cover_image"] = {**CoverImageInput.model_validate(details).model_dump(), "asset_id": cover}
    elif details is ... and cover is not None and previous and previous.get("asset_id") == cover:
        metadata["cover_image"] = previous
    values["metadata_json"] = metadata
