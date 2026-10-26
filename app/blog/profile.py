"""Current-account profile data and the shared server-side publishing gate."""

from fastapi import HTTPException

from app.blog.content import clean_text, safe_url
from app.models.db_models import User


def description_text(value):
    if not isinstance(value, str):
        raise ValueError("Expected a plain-text description.")
    value = "\n".join(clean_text(line, 2000, empty=True) for line in value.splitlines()).strip()
    if len(value) > 2000:
        raise ValueError("Description is too long.")
    return value


def missing_fields(membership):
    missing = []
    for name, validate in (
        ("name", lambda value: clean_text(value, 80)),
        ("avatar_url", safe_url),
        ("designation", lambda value: clean_text(value, 160)),
        ("description", description_text),
    ):
        try:
            if not validate(getattr(membership, name)):
                missing.append(name)
        except (ValueError, TypeError):
            missing.append(name)
    return missing


def require_complete_profile(membership):
    missing = missing_fields(membership)
    if missing:
        labels = ["avatar" if field == "avatar_url" else field for field in missing]
        raise HTTPException(
            422,
            "Complete your author profile before publishing. Missing: " + ", ".join(labels) + ".",
        )


async def profile_data(central, membership):
    owner = await central.get(User, membership.user_id)
    avatar = membership.avatar_url or (owner.avatar_url if owner else None)
    try:
        avatar = safe_url(avatar) if avatar else None
    except ValueError:
        avatar = None
    missing = missing_fields(membership)
    return {
        "name": ((owner.name or "").strip() if owner else "") or membership.name.strip(),
        "designation": membership.designation,
        "avatar_url": avatar,
        "description": membership.description,
        "complete": not missing,
        "missing_fields": missing,
    }
