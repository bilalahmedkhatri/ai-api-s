import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

class FacebookClient:
    """Async client for Facebook Graph API."""

    BASE_URL = "https://graph.facebook.com/v20.0"

    def __init__(self, app_id: str | None = None, app_secret: str | None = None):
        self.app_id = app_id
        self.app_secret = app_secret

    async def publish_post(self, page_id: str, message: str, access_token: str, scheduled_publish_time: int | None = None) -> dict[str, Any]:
        """
        Publish a post to a Facebook Page.
        If scheduled_publish_time is provided (Unix timestamp), the post will be scheduled.
        """
        url = f"{self.BASE_URL}/{page_id}/feed"

        payload = {
            "message": message,
            "access_token": access_token
        }

        if scheduled_publish_time:
            payload["published"] = "false"
            payload["scheduled_publish_time"] = str(scheduled_publish_time)

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=payload)

            if response.status_code != 200:
                logger.error(f"Facebook Graph API Error: {response.text}")
                response.raise_for_status()

            return response.json()

    async def get_page_info(self, page_id: str, access_token: str) -> dict[str, Any]:
        """Fetch basic information about the page (to verify token/page)."""
        url = f"{self.BASE_URL}/{page_id}"

        params = {
            "fields": "id,name,link",
            "access_token": access_token
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.error(f"Facebook Graph API Error: {response.text}")
                response.raise_for_status()

            return response.json()

facebook_client = FacebookClient()
