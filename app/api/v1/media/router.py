from app.api.v1.media.schemas import DeleteMediaRequest, MediaIdentifier, ProcessMediaRequest
from app.services.media_extractor import (
    delete_media_urls,
    extract_and_send_media,
    get_media_urls_for_item,
)
from fastapi import APIRouter, BackgroundTasks, Query, Request, status

router = APIRouter(prefix="/media", tags=["Async Media Extractor"])


@router.post("/process-urls", status_code=status.HTTP_202_ACCEPTED)
async def process_urls(
    request: ProcessMediaRequest, background_tasks: BackgroundTasks, http_request: Request
):
    background_tasks.add_task(
        extract_and_send_media,
        user_id=request.user_id,
        template_id=request.template_id,
        item_id=request.item_id,
        keywords=request.keywords,
        filters=request.filters.model_dump() if request.filters else {},
        authorized_app_id=http_request.state.authorized_app.id,
    )
    return {"status": "accepted", "message": "Background task started"}


@router.get("/{item_id}")
async def get_media(
    item_id: MediaIdentifier,
    http_request: Request,
    user_id: MediaIdentifier = Query(...),
    template_id: MediaIdentifier | None = Query(None),
):
    """Retrieve all media URLs for a specific item_id, template_id and user_id."""
    urls = await get_media_urls_for_item(
        user_id=user_id,
        template_id=template_id,
        item_id=item_id,
        authorized_app_id=http_request.state.authorized_app.id,
    )
    return {"status": "success", "data": urls}


@router.delete("/")
async def delete_media(request: DeleteMediaRequest, http_request: Request):
    """Deletes media files from B2 and the database."""
    if not request.urls:
        return {"status": "success", "deleted": 0, "failed": 0, "message": "No URLs provided"}

    result = await delete_media_urls(
        user_id=request.user_id,
        urls=request.urls,
        authorized_app_id=http_request.state.authorized_app.id,
    )
    return {"status": "success", **result}
