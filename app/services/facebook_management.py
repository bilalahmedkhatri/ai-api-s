"""App-authorized Facebook post management; tokens never leave the gateway."""
import time

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.core.encryption import decrypt_token
from app.models.db_models import FacebookPageToken
from app.services.facebook_client import facebook_client


async def manage_post(db, app_id, page_id, post_id, *, remove=False, message=None, scheduled_publish_time=None):
    record = (await db.execute(select(FacebookPageToken).where(
        FacebookPageToken.page_id == page_id,
        FacebookPageToken.authorized_app_id == app_id,
        FacebookPageToken.is_active.is_(True),
    ))).scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Page connection not found")
    if not page_id.isdecimal() or not all(part.isdecimal() for part in post_id.split("_")):
        raise HTTPException(400, "Invalid page or post ID")
    if scheduled_publish_time is not None and not time.time() + 600 <= scheduled_publish_time <= time.time() + 75 * 86400:
        raise HTTPException(400, "Schedule must be between 10 minutes and 75 days from now")
    if not remove and scheduled_publish_time is None and message is None:
        raise HTTPException(400, "No changes supplied")
    token = decrypt_token(record.encrypted_token)
    url = f"{facebook_client.BASE_URL}/{post_id}"
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            info = await client.get(url, params={"fields": "id,from,is_published,scheduled_publish_time"}, headers={"Authorization": f"Bearer {token}"})
            info.raise_for_status()
            data = info.json()
            # A valid Page token alone does not prove the supplied post belongs to it.
            if str(data.get("from", {}).get("id")) != page_id:
                raise HTTPException(404, "Post not found on this page")
            if data.get("is_published") is not False or float(data.get("scheduled_publish_time", 0)) <= time.time():
                raise HTTPException(409, "Only unpublished future scheduled posts can be changed")
            if remove:
                response = await client.delete(url, headers={"Authorization": f"Bearer {token}"})
            else:
                payload = {}
                if message is not None:
                    payload["message"] = message
                if scheduled_publish_time is not None:
                    payload["scheduled_publish_time"] = str(scheduled_publish_time)
                response = await client.post(url, data=payload, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            if response.json().get("success") is not True:
                raise HTTPException(502, "Remote update was not confirmed")
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Unable to confirm the Facebook post update") from exc
    return {"status": "success", "post_id": post_id}
