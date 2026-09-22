
from pydantic import BaseModel


class MediaFilter(BaseModel):
    orientation: str | None = None
    sort_by: str | None = None
    image_type: str | None = None

class KeywordRequest(BaseModel):
    keyword: str
    quantity_to_download: int | None = 3

class ProcessMediaRequest(BaseModel):
    user_id: str
    item_id: str
    keywords: list[KeywordRequest]
    filters: MediaFilter | None = None
class DeleteMediaRequest(BaseModel):
    user_id: str
    urls: list[str]
