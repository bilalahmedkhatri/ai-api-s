
from pydantic import BaseModel, Field


class FacebookPostRequest(BaseModel):
    page_id: str = Field(..., description="The ID of the Facebook Page")
    message: str = Field(..., description="The content of the post to publish")
    scheduled_publish_time: int | None = Field(
        None,
        description="Optional Unix timestamp for scheduling the post. Must be between 10 minutes and 75 days from now."
    )

class FacebookPostResponse(BaseModel):
    status: str
    post_id: str | None = None
    error: str | None = None

class TokenStoreRequest(BaseModel):
    page_id: str
    page_name: str | None = None
    access_token: str

class TokenStoreResponse(BaseModel):
    status: str
    message: str
