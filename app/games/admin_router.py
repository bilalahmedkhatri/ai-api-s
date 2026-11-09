"""Dashboard-only game CRUD and moderation; no delete operations."""

from datetime import UTC, datetime

import sqlalchemy as sa
from app.games.contracts import GameCommentResponse, GameInput, GamePatch, GameResponse, ModerationInput
from app.games.database import games_session
from app.games.schema import game_comments, games
from app.services.dashboard import check_origin, member, require_admin
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

router = APIRouter(prefix="/dashboard/games", tags=["dashboard"])


async def database():
    try:
        async with games_session() as session:
            yield session
    except SQLAlchemyError:
        raise HTTPException(503, "Games database is temporarily unavailable.") from None
    except ValueError:
        raise HTTPException(503, "Games database is not configured.") from None


def row_dict(row):
    return dict(row._mapping)


async def admin(membership=Depends(member)):
    require_admin(membership)
    return membership


@router.get("", response_model=list[GameResponse])
async def list_games(
    publish_status: str | None = Query(default=None, pattern="^(draft|published)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    _admin=Depends(admin),
    session=Depends(database),
):
    statement = sa.select(games)
    if publish_status:
        statement = statement.where(games.c.publish_status == publish_status)
    statement = statement.order_by(games.c.updated_at.desc()).limit(limit).offset(offset)
    return [row_dict(row) for row in (await session.execute(statement)).all()]


@router.post("", status_code=201, response_model=GameResponse)
async def create_game(body: GameInput, request: Request, _admin=Depends(admin), session=Depends(database)):
    check_origin(request)
    values = body.model_dump()
    if values["publish_status"] == "published":
        values["published_at"] = datetime.now(UTC)
    try:
        row = (await session.execute(sa.insert(games).values(**values).returning(games))).one()
        await session.commit()
        return row_dict(row)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Game ID or slug already exists.") from None


@router.patch("/{game_id}", response_model=GameResponse)
async def update_game(game_id: str, body: GamePatch, request: Request, _admin=Depends(admin), session=Depends(database)):
    check_origin(request)
    values = body.model_dump(exclude_unset=True)
    current = (await session.execute(sa.select(games.c.publish_status).where(games.c.id == game_id))).one_or_none()
    if current is None:
        raise HTTPException(404, "Game not found.")
    values["updated_at"] = datetime.now(UTC)
    if values.get("release_date") is not None and "release_window" not in values:
        values["release_window"] = None
    if values.get("release_window") is not None and "release_date" not in values:
        values["release_date"] = None
    if values.get("publish_status") == "published" and current.publish_status != "published":
        values["published_at"] = datetime.now(UTC)
    try:
        row = (await session.execute(sa.update(games).where(games.c.id == game_id).values(**values).returning(games))).one()
        await session.commit()
        return row_dict(row)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Game slug conflicts with another record.") from None


@router.get("/{game_id}/comments", response_model=list[GameCommentResponse])
async def list_game_comments(
    game_id: str,
    moderation_status: str | None = Query(default=None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    _admin=Depends(admin),
    session=Depends(database),
):
    statement = sa.select(game_comments).where(game_comments.c.game_id == game_id)
    if moderation_status:
        statement = statement.where(game_comments.c.moderation_status == moderation_status)
    statement = statement.order_by(game_comments.c.created_at.desc(), game_comments.c.id.desc()).limit(limit).offset(offset)
    return [row_dict(row) for row in (await session.execute(statement)).all()]


@router.patch("/comments/{comment_id}", response_model=GameCommentResponse)
async def moderate_comment(comment_id: str, body: ModerationInput, request: Request, _admin=Depends(admin), session=Depends(database)):
    check_origin(request)
    row = (await session.execute(sa.update(game_comments).where(game_comments.c.id == comment_id).values(
        moderation_status=body.moderation_status
    ).returning(game_comments))).one_or_none()
    if row is None:
        raise HTTPException(404, "Comment not found.")
    await session.commit()
    return row_dict(row)
