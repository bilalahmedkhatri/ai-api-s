"""Short-lived async sessions for the independently configured Games database."""

from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.games.config import games_database_url


def create_games_engine(url):
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        poolclass=NullPool,
        connect_args={
            "prepared_statement_name_func": lambda: f"__games_{uuid4().hex}__",
            "timeout": 10,
            "command_timeout": 30,
            "server_settings": {"application_name": "ai-gateway-games"},
        },
    )


@asynccontextmanager
async def games_session():
    engine = create_games_engine(games_database_url().get_secret_value())
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
