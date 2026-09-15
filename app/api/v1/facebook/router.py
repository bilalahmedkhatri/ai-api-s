import logging

import httpx
from app.api.v1.facebook.schemas import (
    FacebookPostRequest,
    FacebookPostResponse,
    TokenStoreRequest,
    TokenStoreResponse,
)
from app.core.encryption import decrypt_token, encrypt_token
from app.db.database import get_db
from app.models.db_models import FacebookPageToken
from app.services.facebook_client import facebook_client
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/facebook", tags=["Facebook Integrations"])

@router.post("/store-token", response_model=TokenStoreResponse)
async def store_page_token(
    request: TokenStoreRequest,
    db: AsyncSession = Depends(get_db)
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
                encrypted_token=encrypted
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
    request: FacebookPostRequest,
    db: AsyncSession = Depends(get_db)
):
    """Publish a new post to a Facebook Page."""
    # Retrieve token
    stmt = select(FacebookPageToken).where(
        FacebookPageToken.page_id == request.page_id,
        FacebookPageToken.is_active == True # noqa: E712
    )
    result = await db.execute(stmt)
    token_record = result.scalars().first()

    if not token_record:
        raise HTTPException(status_code=404, detail="No active token found for this page ID. Please connect your page first.")

    try:
        access_token = decrypt_token(token_record.encrypted_token)

        fb_response = await facebook_client.publish_post(
            page_id=request.page_id,
            message=request.message,
            access_token=access_token,
            scheduled_publish_time=request.scheduled_publish_time
        )

        return FacebookPostResponse(
            status="success",
            post_id=fb_response.get("id")
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to publish to Facebook: {e.response.text}")
        error_msg = e.response.json().get("error", {}).get("message", "Unknown Facebook Error")
        raise HTTPException(status_code=400, detail=f"Facebook API Error: {error_msg}") from e
    except Exception as e:
        logger.error(f"Error publishing post: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error") from e
