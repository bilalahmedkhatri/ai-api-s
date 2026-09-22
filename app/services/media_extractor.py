import asyncio
import logging
import os
import uuid
from urllib.parse import quote_plus

import httpx
from botocore.exceptions import ClientError
from sqlalchemy.future import select
from sqlalchemy import delete

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.db_models import ExtractedMedia

logger = logging.getLogger(__name__)

MAX_MEDIA_PER_PROVIDER = 3

# This assumes aioboto3 is installed. We need to import it here.
# It will fail on load if not installed, so the user must install it.
try:
    import aioboto3
except ImportError:
    aioboto3 = None


from botocore.config import Config

def get_b2_session():
    if not aioboto3:
        raise RuntimeError("aioboto3 is not installed. Run `uv add aioboto3`.")
    
    # Extract region from endpoint (e.g. https://s3.eu-central-003.backblazeb2.com -> eu-central-003)
    endpoint = settings.b2_endpoint_url or ""
    region = "us-east-1"
    if "s3." in endpoint and ".backblazeb2" in endpoint:
        region = endpoint.split("s3.")[1].split(".backblazeb2")[0]

    return aioboto3.Session(
        aws_access_key_id=settings.b2_application_key_id,
        aws_secret_access_key=settings.b2_application_key,
        region_name=region
    ), Config(signature_version='s3v4')


async def fetch_pexels(keyword: str, quantity: int, filters: dict, client: httpx.AsyncClient) -> list[str]:
    if not settings.pexels_api_key:
        return []

    is_video = filters.get("image_type", "").lower() == "video"
    if is_video:
        url = f"https://api.pexels.com/videos/search?query={quote_plus(keyword)}&per_page={quantity}"
    else:
        url = f"https://api.pexels.com/v1/search?query={quote_plus(keyword)}&per_page={quantity}"

    if filters.get("orientation"):
        ori = filters["orientation"].lower()
        if ori in ["landscape", "portrait", "square"]:
            url += f"&orientation={ori}"

    headers = {"Authorization": settings.pexels_api_key}

    try:
        response = await client.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        media_urls = []
        if is_video:
            for video in data.get("videos", [])[:quantity]:
                files = video.get("video_files", [])
                if files:
                    media_urls.append(files[0].get("link"))
        else:
            for photo in data.get("photos", [])[:quantity]:
                img = photo.get("src", {}).get("original") or photo.get("src", {}).get("large")
                if img:
                    media_urls.append(img)
        return media_urls
    except Exception as exc:
        logger.error("Pexels API error for keyword %s: %s", keyword, exc)
        return []


async def fetch_pixabay(keyword: str, quantity: int, filters: dict, client: httpx.AsyncClient) -> list[str]:
    if not settings.pixabay_api_key:
        return []

    is_video = filters.get("image_type", "").lower() == "video"
    base_path = "/api/videos/" if is_video else "/api/"
    
    url = f"https://pixabay.com{base_path}?key={settings.pixabay_api_key}&q={quote_plus(keyword)}&per_page={max(quantity, 3)}"

    if filters.get("orientation"):
        ori = filters["orientation"].lower()
        if ori == "landscape":
            url += "&orientation=horizontal"
        elif ori == "portrait":
            url += "&orientation=vertical"

    if filters.get("sort_by"):
        sort_by = filters["sort_by"].lower()
        if sort_by in ["popular", "favorite", "most_downloaded", "most downloaded"]:
            url += "&order=popular"
        elif sort_by in ["latest", "new"]:
            url += "&order=latest"

    if not is_video and filters.get("image_type"):
        img_type = filters["image_type"].lower()
        if img_type in ["photo", "illustration", "vector"]:
            url += f"&image_type={img_type}"

    try:
        response = await client.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()

        media_urls = []
        for hit in data.get("hits", [])[:quantity]:
            if is_video:
                videos = hit.get("videos", {})
                vid = videos.get("medium", {}).get("url") or videos.get("large", {}).get("url") or videos.get("small", {}).get("url")
                if vid:
                    media_urls.append(vid)
            else:
                img = hit.get("largeImageURL") or hit.get("webformatURL")
                if img:
                    media_urls.append(img)
        return media_urls
    except Exception as exc:
        logger.error("Pixabay API error for keyword %s: %s", keyword, exc)
        return []


async def upload_image_to_b2(url: str, user_id: str, item_id: str, client: httpx.AsyncClient) -> str | None:
    try:
        response = await client.get(url, timeout=15)
        response.raise_for_status()
        
        # Generate object key
        filename = os.path.basename(url.split("?")[0])
        if not filename or "." not in filename:
            filename = f"media_{uuid.uuid4().hex[:8]}.jpg"
        else:
            name, ext = os.path.splitext(filename)
            filename = f"{name}_{uuid.uuid4().hex[:8]}{ext}"
            
        object_key = f"media/{user_id}/{item_id}/{filename}"
        
        # Upload to B2
        session, b2_config = get_b2_session()
        async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=b2_config) as s3:
            await s3.put_object(
                Bucket=settings.b2_bucket_name,
                Key=object_key,
                Body=response.content,
                ContentType=response.headers.get("Content-Type", "image/jpeg")
            )
            
        logger.info("Saved media to B2: %s", object_key)
        return object_key
    except Exception as exc:
        logger.error("Failed to upload image %s to B2: %s", url, exc)
        return None


async def extract_and_send_media(user_id: str, item_id: str, keywords: list, filters: dict):
    extracted_keys = []

    # Use a standard browser User-Agent to avoid 503/403 blocks from CDNs
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(headers=default_headers) as client:
        for k_obj in keywords:
            keyword = k_obj.keyword
            quantity = k_obj.quantity_to_download or 3
            
            # Add a 1.5-second delay to avoid hitting rate limits on Pexels/Pixabay
            await asyncio.sleep(1.5)

            pexels_urls = await fetch_pexels(keyword, quantity, filters, client)
            pixabay_urls = await fetch_pixabay(keyword, quantity, filters, client)

            all_urls = pexels_urls + pixabay_urls

            for media_url in all_urls:
                object_key = await upload_image_to_b2(media_url, user_id, item_id, client)
                if object_key:
                    extracted_keys.append(object_key)
        
    # Save to Database
    if extracted_keys:
        async with AsyncSessionLocal() as db_session:
            for key in extracted_keys:
                media_record = ExtractedMedia(
                    user_id=user_id,
                    item_id=item_id,
                    object_key=key
                )
                db_session.add(media_record)
            await db_session.commit()
            logger.info("Saved %d media keys to DB for item %s", len(extracted_keys), item_id)


async def get_media_urls_for_item(user_id: str, item_id: str) -> list[str]:
    """Fetch object keys from DB and generate pre-signed S3 URLs."""
    async with AsyncSessionLocal() as db_session:
        result = await db_session.execute(
            select(ExtractedMedia).where(
                ExtractedMedia.user_id == user_id,
                ExtractedMedia.item_id == item_id
            )
        )
        media_records = result.scalars().all()
        
    if not media_records:
        return []
        
    session, b2_config = get_b2_session()
    urls = []
    async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=b2_config) as s3:
        for record in media_records:
            try:
                url = await s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': settings.b2_bucket_name, 'Key': record.object_key},
                    ExpiresIn=3600  # 1 hour
                )
                urls.append(url)
            except ClientError as e:
                logger.error("Failed to generate presigned URL for %s: %s", record.object_key, e)
                
    return urls

import urllib.parse

async def delete_media_urls(user_id: str, urls: list[str]) -> dict:
    """Extracts object keys from URLs and deletes them from B2 and the Database."""
    keys_to_delete = []
    bucket_prefix = f"/{settings.b2_bucket_name}/"
    
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        if path.startswith(bucket_prefix):
            object_key = urllib.parse.unquote(path[len(bucket_prefix):])
            # Security check: ensure the object key belongs to the user
            if object_key.startswith(f"media/{user_id}/"):
                keys_to_delete.append(object_key)
                
    if not keys_to_delete:
        return {"deleted": 0, "failed": 0, "message": "No valid URLs found for this user."}

    session, b2_config = get_b2_session()
    deleted_count = 0
    failed_count = 0
    
    async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=b2_config) as s3:
        for key in keys_to_delete:
            try:
                await s3.delete_object(Bucket=settings.b2_bucket_name, Key=key)
                deleted_count += 1
            except Exception as e:
                logger.error("Failed to delete %s from B2: %s", key, e)
                failed_count += 1
                
    # Delete from DB if any were deleted
    if keys_to_delete:
        async with AsyncSessionLocal() as db_session:
            await db_session.execute(
                delete(ExtractedMedia).where(
                    ExtractedMedia.user_id == user_id,
                    ExtractedMedia.object_key.in_(keys_to_delete)
                )
            )
            await db_session.commit()
            
    return {"deleted": deleted_count, "failed": failed_count}
