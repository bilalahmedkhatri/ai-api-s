"""Server-only BondsGame database configuration."""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy.engine import make_url

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
DATABASE_VARIABLE = "GAMES_BONDSGAME_DATABASE_URL"


class GamesConfigurationError(ValueError):
    """Safe configuration error without database credentials."""


def games_database_url(environment=None) -> SecretStr:
    values = {**dotenv_values(ENV_FILE), **os.environ} if environment is None else environment
    raw_url = (values.get(DATABASE_VARIABLE) or "").strip()
    try:
        parsed = make_url(raw_url)
        valid = (
            parsed.drivername in {"postgres", "postgresql", "postgresql+asyncpg"}
            and bool(parsed.host and parsed.database and parsed.username and parsed.password)
            and not any(ord(char) < 32 or ord(char) == 127 for char in raw_url)
            and set(parsed.query) <= {"ssl", "sslmode", "channel_binding", "target_session_attrs", "charset"}
        )
    except Exception:
        valid = False
    if not valid:
        raise GamesConfigurationError(f"{DATABASE_VARIABLE} is not configured as a PostgreSQL URL.")
    query = dict(parsed.query)
    ssl = query.pop("sslmode", query.get("ssl", "require"))
    if ssl not in {"require", "verify-ca", "verify-full"} or ("ssl" in query and query["ssl"] != ssl):
        raise GamesConfigurationError(f"{DATABASE_VARIABLE} must require TLS.")
    query.pop("channel_binding", None)
    query.pop("target_session_attrs", None)
    query.pop("charset", None)
    query["ssl"] = ssl
    return SecretStr(parsed.set(drivername="postgresql+asyncpg", query=query).render_as_string())
