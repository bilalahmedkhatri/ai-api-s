"""Search API router."""

from fastapi import APIRouter

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/")
def search_root() -> dict[str, str]:
    return {"message": "Search router is ready"}
