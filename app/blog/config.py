"""Independent server-owned blog apps; never use gateway authorized_apps for routing."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy.engine import make_url

APP_LABELS = {
    "azeemlab": "AzeemLab",
    "ailettergen": "AILetterGen",
    "bondsgame": "BondsGame",
    "sikanderart": "Sikander Art",
    "e_sahulat": "E Sahulat",
    "genzee_video": "GenZee Video",
}
APP_KEYS = tuple(APP_LABELS)
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class BlogConfigurationError(ValueError):
    """Safe to display without database credentials."""


def _database_url(value: str, variable: str) -> SecretStr:
    valid = False
    normalized = ""
    try:
        url = make_url(value)
        valid = (
            url.drivername in {"postgres", "postgresql", "postgresql+asyncpg"}
            and bool(url.host and url.database and url.username and url.password)
            and not any(ord(char) < 32 or ord(char) == 127 for char in value)
            and set(url.query)
            <= {"ssl", "sslmode", "channel_binding", "target_session_attrs", "charset"}
        )
        query = dict(url.query)
        ssl = query.pop("sslmode", query.get("ssl", "require"))
        valid = valid and ssl in {"require", "verify-ca", "verify-full"}
        if "ssl" in query and query["ssl"] != ssl:
            valid = False
        query.pop("charset", None)
        query.pop("channel_binding", None)
        query.pop("target_session_attrs", None)
        query["ssl"] = ssl
        normalized = url.set(drivername="postgresql+asyncpg", query=query).render_as_string(
            hide_password=False
        )
    except Exception:
        valid = False
    if not valid:
        raise BlogConfigurationError(
            f"{variable}: use a PostgreSQL URL with credentials, host, database and required TLS."
        )
    return SecretStr(normalized)


@dataclass(frozen=True)
class BlogTarget:
    key: str
    label: str
    database_url: SecretStr | None
    public_blog_url: str | None = None

    @property
    def ready(self) -> bool:
        return self.database_url is not None


@dataclass(frozen=True)
class BlogRegistry:
    targets: tuple[BlogTarget, ...]

    def for_app(self, key: str) -> BlogTarget:
        # Routing is not authorization; handlers must check membership and app scope first.
        if type(key) is not str or key not in APP_KEYS:
            raise BlogConfigurationError("Unknown blog app.")
        target = next((item for item in self.targets if item.key == key), None)
        if target is None or not target.ready:
            raise BlogConfigurationError("Blog storage is not configured for this app.")
        return target


def load_registry(environment: Mapping[str, str | None] | None = None) -> BlogRegistry:
    """Read backend settings only. Legacy BLOG_*_APP_ID values are deliberately ignored."""
    values = {**dotenv_values(ENV_FILE), **os.environ} if environment is None else environment
    targets = []
    for key, label in APP_LABELS.items():
        variable = f"BLOG_{key.upper()}_DATABASE_URL"
        raw_url = (values.get(variable) or "").strip()
        secret = _database_url(raw_url, variable) if raw_url else None
        public_url = (values.get(f"BLOG_{key.upper()}_PUBLIC_BLOG_URL") or "").strip().rstrip(
            "/"
        ) or None
        if public_url is not None:
            try:
                parsed = urlsplit(public_url)
                valid = (
                    parsed.scheme == "https"
                    and bool(parsed.hostname)
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and not any(char.isspace() or ord(char) < 32 for char in public_url)
                    and "\\" not in public_url
                    and "{" not in public_url.replace("{locale}", "")
                    and "}" not in public_url.replace("{locale}", "")
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise BlogConfigurationError(
                    f"BLOG_{key.upper()}_PUBLIC_BLOG_URL: use a confirmed credential-free HTTPS blog URL."
                )
        targets.append(BlogTarget(key, label, secret, public_url))
    return BlogRegistry(tuple(targets))
