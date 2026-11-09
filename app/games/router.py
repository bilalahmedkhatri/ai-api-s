"""Authenticated server-to-server read API and safe public comment submission."""

from uuid import uuid4

import sqlalchemy as sa
from app.core.auth import get_current_api_key
from app.games.contracts import CommentInput, GameCommentResponse, GameResponse, SimilarGameResponse
from app.games.database import games_session
from app.games.schema import game_comments, games
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix="/games", tags=["BondsGame"])


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


@router.get("", response_model=list[GameResponse], dependencies=[Depends(get_current_api_key)])
async def list_games(
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    genre: str | None = Query(default=None, max_length=64),
    session=Depends(database),
):
    statement = sa.select(games).where(games.c.publish_status == "published")
    if genre:
        statement = statement.where(games.c.genres.overlap([genre]))
    statement = statement.order_by(games.c.is_featured.desc(), games.c.updated_at.desc()).limit(limit).offset(offset)
    return [row_dict(row) for row in (await session.execute(statement)).all()]


@router.get("/{slug}", response_model=GameResponse, dependencies=[Depends(get_current_api_key)])
async def get_game(slug: str, session=Depends(database)):
    row = (await session.execute(sa.select(games).where(
        games.c.slug == slug, games.c.publish_status == "published"
    ))).one_or_none()
    if row is None:
        raise HTTPException(404, "Game not found.")
    return row_dict(row)


@router.get("/{slug}/similar", response_model=list[SimilarGameResponse], dependencies=[Depends(get_current_api_key)])
async def similar_games(
    slug: str,
    limit: int = Query(default=4, ge=1, le=4),
    session=Depends(database),
):
    source = (await session.execute(sa.select(games.c.id, games.c.genres).where(
        games.c.slug == slug, games.c.publish_status == "published"
    ))).mappings().one_or_none()
    if source is None:
        raise HTTPException(404, "Game not found.")
    if not source["genres"]:
        return []
    statement = (
        sa.select(
            games.c.id, games.c.slug, games.c.title, games.c.excerpt,
            games.c.cover_image, games.c.hero_image, games.c.genres,
            games.c.platforms, games.c.release_date, games.c.release_status,
            games.c.editorial_score,
        )
        .where(games.c.publish_status == "published", games.c.id != source["id"], games.c.genres.overlap(source["genres"]))
        .order_by(games.c.is_featured.desc(), games.c.editorial_score.desc().nullslast(), games.c.title)
        .limit(limit)
    )
    return [row_dict(row) for row in (await session.execute(statement)).all()]


@router.get("/{slug}/comments", response_model=list[GameCommentResponse], dependencies=[Depends(get_current_api_key)])
async def list_comments(
    slug: str,
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None, max_length=64),
    session=Depends(database),
):
    game_id = (await session.execute(sa.select(games.c.id).where(
        games.c.slug == slug, games.c.publish_status == "published"
    ))).scalar_one_or_none()
    if game_id is None:
        raise HTTPException(404, "Game not found.")
    statement = sa.select(game_comments).where(
        game_comments.c.game_id == game_id, game_comments.c.moderation_status == "approved"
    )
    if before:
        cursor = (await session.execute(sa.select(game_comments.c.created_at, game_comments.c.id).where(
            game_comments.c.id == before, game_comments.c.game_id == game_id
        ))).one_or_none()
        if cursor:
            statement = statement.where(sa.tuple_(game_comments.c.created_at, game_comments.c.id) < (cursor.created_at, cursor.id))
    statement = statement.order_by(game_comments.c.created_at.desc(), game_comments.c.id.desc()).limit(limit)
    return [row_dict(row) for row in (await session.execute(statement)).all()]


@router.post("/{slug}/comments", status_code=202, response_model=GameCommentResponse)
async def submit_comment(
    slug: str,
    body: CommentInput,
    _api_key=Depends(get_current_api_key),
    session=Depends(database),
):
    game_id = (await session.execute(sa.select(games.c.id).where(
        games.c.slug == slug, games.c.publish_status == "published"
    ))).scalar_one_or_none()
    if game_id is None:
        raise HTTPException(404, "Game not found.")
    values = body.model_dump()
    values.update(id=f"gc-{uuid4().hex}", game_id=game_id, moderation_status="pending")
    row = (await session.execute(sa.insert(game_comments).values(**values).returning(game_comments))).one()
    await session.commit()
    return row_dict(row)
