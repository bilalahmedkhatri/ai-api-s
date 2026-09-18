import json
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

    async def publish_post(
        self,
        page_id: str,
        message: str,
        access_token: str,
        scheduled_publish_time: int | None = None,
        button_type: str | None = None,
        button_link: str | None = None,
    ) -> dict[str, Any]:
        """
        Publish a plain text post to a Facebook Page.
        Optionally schedule it or attach a CTA button.
        """
        url = f"{self.BASE_URL}/{page_id}/feed"
        payload: dict[str, Any] = {"message": message, "access_token": access_token}

        if scheduled_publish_time:
            payload["published"] = "false"
            payload["scheduled_publish_time"] = str(scheduled_publish_time)

        if button_type and button_link:
            payload["call_to_action"] = json.dumps({
                "type": button_type,
                "value": {"link": button_link},
            })

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=payload)
            if response.status_code != 200:
                logger.error(f"Facebook Graph API Error: {response.text}")
                response.raise_for_status()
            return response.json()

    async def publish_photo(
        self,
        page_id: str,
        message: str,
        access_token: str,
        photo_bytes: bytes,
        filename: str,
        content_type: str,
        scheduled_publish_time: int | None = None,
        button_type: str | None = None,
        button_link: str | None = None,
    ) -> dict[str, Any]:
        """Upload a photo and post it to a Facebook Page."""
        # 1. Upload photo as unpublished/temporary
        photo_url = f"{self.BASE_URL}/{page_id}/photos"
        photo_fields: dict[str, Any] = {
            "access_token": access_token,
            "published": "false",
            "temporary": "true"
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            photo_resp = await client.post(
                photo_url,
                data=photo_fields,
                files={"source": (filename, photo_bytes, content_type)},
            )
            if photo_resp.status_code != 200:
                logger.error(f"Facebook Graph API Error (photo upload): {photo_resp.text}")
                photo_resp.raise_for_status()
            
            photo_id = photo_resp.json().get("id")

        # 2. Publish to feed with attached media
        feed_url = f"{self.BASE_URL}/{page_id}/feed"
        feed_fields: dict[str, Any] = {
            "message": message,
            "access_token": access_token,
            "attached_media": json.dumps([{"media_fbid": photo_id}])
        }

        if scheduled_publish_time:
            feed_fields["published"] = "false"
            feed_fields["scheduled_publish_time"] = str(scheduled_publish_time)

        if button_type and button_link:
            feed_fields["call_to_action"] = json.dumps({
                "type": button_type,
                "value": {"link": button_link},
            })

        async with httpx.AsyncClient(timeout=30.0) as client:
            feed_resp = await client.post(feed_url, data=feed_fields)
            if feed_resp.status_code != 200:
                logger.error(f"Facebook Graph API Error (photo feed): {feed_resp.text}")
                feed_resp.raise_for_status()
            return feed_resp.json()

    async def publish_video(
        self,
        page_id: str,
        message: str,
        access_token: str,
        video_bytes: bytes,
        filename: str,
        content_type: str,
        scheduled_publish_time: int | None = None,
        button_type: str | None = None,
        button_link: str | None = None,
    ) -> dict[str, Any]:
        """Upload a video and post it to a Facebook Page."""
        url = f"{self.BASE_URL}/{page_id}/videos"

        fields: dict[str, Any] = {
            "description": message,
            "access_token": access_token,
        }
        if scheduled_publish_time:
            fields["published"] = "false"
            fields["scheduled_publish_time"] = str(scheduled_publish_time)
        if button_type and button_link:
            fields["call_to_action"] = json.dumps({
                "type": button_type,
                "value": {"link": button_link},
            })

        async with httpx.AsyncClient(timeout=300.0) as client:  # Videos can be large
            response = await client.post(
                url,
                data=fields,
                files={"source": (filename, video_bytes, content_type)},
            )
            if response.status_code != 200:
                logger.error(f"Facebook Graph API Error (video): {response.text}")
                response.raise_for_status()
            return response.json()

    async def get_page_info(self, page_id: str, access_token: str) -> dict[str, Any]:
        """Fetch basic information about the page (to verify token/page)."""
        url = f"{self.BASE_URL}/{page_id}"
        params = {"fields": "id,name,link", "access_token": access_token}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                logger.error(f"Facebook Graph API Error: {response.text}")
                response.raise_for_status()
            return response.json()


facebook_client = FacebookClient()
