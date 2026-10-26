from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def safe_identifier(value):
    if value in {".", ".."} or any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use a single media identifier")
    return value


MediaIdentifier = Annotated[
    str, Field(min_length=1, max_length=128), AfterValidator(safe_identifier)
]


class MediaFilter(BaseModel):
    orientation: str | None = None
    sort_by: str | None = None
    image_type: str | None = None


class KeywordRequest(BaseModel):
    keyword: str
    quantity_to_download: int | None = 3


class ProcessMediaRequest(BaseModel):
    user_id: MediaIdentifier
    template_id: MediaIdentifier | None = None
    item_id: MediaIdentifier
    keywords: list[KeywordRequest]
    filters: MediaFilter | None = None


class DeleteMediaRequest(BaseModel):
    user_id: MediaIdentifier
    urls: list[str]
