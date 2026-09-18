import logging
from enum import StrEnum

import httpx
from app.api.v1.facebook.schemas import (
    FacebookPostResponse,
    TokenStoreRequest,
    TokenStoreResponse,
)
from app.core.encryption import decrypt_token, encrypt_token
from app.db.database import get_db
from app.models.db_models import FacebookPageToken
from app.services.facebook_client import facebook_client
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/facebook", tags=["Facebook Integrations"])

# Supported MIME types
_PHOTO_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/avi", "video/x-matroska", "video/webm"}


class ButtonType(StrEnum):
    """Supported Facebook Page CTA button types."""
    NONE = "NONE"
    LEARN_MORE = "LEARN_MORE"
    SHOP_NOW = "SHOP_NOW"
    SIGN_UP = "SIGN_UP"
    BOOK_NOW = "BOOK_NOW"
    CONTACT_US = "CONTACT_US"
    SEND_MESSAGE = "MESSAGE_PAGE"


@router.post("/store-token", response_model=TokenStoreResponse)
async def store_page_token(
    request: TokenStoreRequest,
    db: AsyncSession = Depends(get_db),
):
    """Store an encrypted Facebook Page access token."""
    try:
        # Verify the token is valid for the page
        page_info = await facebook_client.get_page_info(request.page_id, request.access_token)

        # Check if already exists
        stmt = select(FacebookPageToken).where(FacebookPageToken.page_id == request.page_id)
        result = await db.execute(stmt)
        token_record = result.scalars().first()

        encrypted = encrypt_token(request.access_token)

        if token_record:
            token_record.encrypted_token = encrypted
            token_record.page_name = request.page_name or page_info.get("name")
        else:
            token_record = FacebookPageToken(
                page_id=request.page_id,
                page_name=request.page_name or page_info.get("name"),
                encrypted_token=encrypted,
            )
            db.add(token_record)

        await db.commit()
        return TokenStoreResponse(status="success", message="Token stored securely.")

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to verify token: {e.response.text}")
        raise HTTPException(status_code=400, detail="Invalid token or page ID") from e
    except Exception as e:
        logger.error(f"Error storing token: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/post-pages", response_model=FacebookPostResponse)
async def post_to_page(
    page_id: str = Form(..., description="The ID of the Facebook Page"),
    message: str = Form(..., description="The text content of the post"),
    scheduled_publish_time: int | None = Form(None, description="Optional Unix timestamp for scheduling (10 min – 75 days from now)"),
    button_type: ButtonType = Form(ButtonType.NONE, description="Optional CTA button type"),
    button_link: str | None = Form(None, description="URL for the CTA button (required when button_type is set)"),
    media: UploadFile | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Publish a post to a Facebook Page.
    Accepts optional media (photo or video) as a file upload.
    Supports scheduling via Unix timestamp and optional CTA buttons.
    """
    # Retrieve stored token
    stmt = select(FacebookPageToken).where(
        FacebookPageToken.page_id == page_id,
        FacebookPageToken.is_active == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    token_record = result.scalars().first()

    if not token_record:
        raise HTTPException(
            status_code=404,
            detail="No active token found for this page ID. Please connect your page first.",
        )

    try:
        access_token = decrypt_token(token_record.encrypted_token)

        # Resolve button params
        btn_type = button_type.value if button_type != ButtonType.NONE else None
        btn_link = button_link if btn_type else None

        if media and media.filename:
            content_type = media.content_type or ""
            media_bytes = await media.read()

            if content_type in _PHOTO_TYPES:
                fb_response = await facebook_client.publish_photo(
                    page_id=page_id,
                    message=message,
                    access_token=access_token,
                    photo_bytes=media_bytes,
                    filename=media.filename,
                    content_type=content_type,
                    scheduled_publish_time=scheduled_publish_time,
                    button_type=btn_type,
                    button_link=btn_link,
                )
            elif content_type in _VIDEO_TYPES:
                fb_response = await facebook_client.publish_video(
                    page_id=page_id,
                    message=message,
                    access_token=access_token,
                    video_bytes=media_bytes,
                    filename=media.filename,
                    content_type=content_type,
                    scheduled_publish_time=scheduled_publish_time,
                    button_type=btn_type,
                    button_link=btn_link,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported media type '{content_type}'. Supported: JPEG, PNG, GIF, WEBP, MP4, MOV, AVI, MKV, WEBM.",
                )
        else:
            # Plain text post
            fb_response = await facebook_client.publish_post(
                page_id=page_id,
                message=message,
                access_token=access_token,
                scheduled_publish_time=scheduled_publish_time,
                button_type=btn_type,
                button_link=btn_link,
            )

        return FacebookPostResponse(status="success", post_id=fb_response.get("id"))

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to publish to Facebook: {e.response.text}")
        error_msg = e.response.json().get("error", {}).get("message", "Unknown Facebook Error")
        raise HTTPException(status_code=400, detail=f"Facebook API Error: {error_msg}") from e
    except Exception as e:
        logger.error(f"Error publishing post: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error") from e

