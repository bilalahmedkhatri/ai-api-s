"""One transactional implementation for every independent app database."""

import contextlib
import copy
import logging
import unicodedata
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.blog.content import (
    MEDIA_TYPES,
    clean_text,
    document_nodes,
    plain_text,
    safe_url,
    slug_base,
)
from app.blog.cover_image import apply_cover_details
from app.blog.metadata import build_metadata, clip
from app.blog.profile import description_text
from app.blog.schema import blog_assets as assets
from app.blog.schema import blog_categories as categories
from app.blog.schema import blog_posts as posts
from app.models.db_models import User
from app.services.dashboard import audit

logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(UTC)


async def find_post(db, identifier):
    row = (
        (await db.execute(sa.select(posts).where(posts.c.id == identifier)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Blog post not found.")
    return dict(row)


async def list_posts(db, *, page=1, page_size=20, status=None, locale=None, search=None):
    conditions = []
    if status:
        conditions.append(posts.c.status == status)
    if locale:
        conditions.append(posts.c.locale == locale)
    if search:
        conditions.append(
            sa.or_(
                posts.c.title.icontains(search, autoescape=True),
                posts.c.slug.icontains(search, autoescape=True),
            )
        )
    total = (
        await db.execute(sa.select(sa.func.count()).select_from(posts).where(*conditions))
    ).scalar_one()
    statement = (
        sa.select(*(column for column in posts.c if column.name != "content_json"))
        .where(*conditions)
        .order_by(posts.c.updated_at.desc(), posts.c.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [dict(row) for row in (await db.execute(statement)).mappings().all()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def list_categories(db, *, search=None, page=1, page_size=50):
    condition = []
    if search:
        condition.append(
            categories.c.normalized_name.contains(
                unicodedata.normalize("NFKC", search).casefold(), autoescape=True
            )
        )
    total = (
        await db.execute(sa.select(sa.func.count()).select_from(categories).where(*condition))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                sa.select(categories)
                .where(*condition)
                .order_by(categories.c.normalized_name, categories.c.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def category_for_name(db, name):
    normalized = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    if not normalized or len(normalized) > 160:
        raise HTTPException(422, "Category name exceeds supported limits.")
    existing = (
        (await db.execute(sa.select(categories).where(categories.c.normalized_name == normalized)))
        .mappings()
        .one_or_none()
    )
    if existing:
        return dict(existing), False
    base = slug_base(name, 140)
    for attempt in range(8):
        slug = base if attempt == 0 else f"{base}-{uuid.uuid4().hex[:8]}"
        try:
            async with db.begin_nested():
                row = (
                    (
                        await db.execute(
                            categories.insert()
                            .values(name=name, normalized_name=normalized, slug=slug)
                            .returning(*categories.c)
                        )
                    )
                    .mappings()
                    .one()
                )
            return dict(row), True
        except IntegrityError:
            existing = (
                (
                    await db.execute(
                        sa.select(categories).where(categories.c.normalized_name == normalized)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                return dict(existing), False
            if not (
                await db.execute(sa.select(categories.c.id).where(categories.c.slug == slug))
            ).first():
                raise
    raise HTTPException(409, "Category slug could not be allocated. Retry the operation.")


async def category_id(db, values, current=None):
    if values.get("category_name") is not None:
        row, _ = await category_for_name(db, values["category_name"])
        return row["id"]
    identifier = values.get("category_id", current)
    if (
        identifier is not None
        and not (
            await db.execute(sa.select(categories.c.id).where(categories.c.id == identifier))
        ).first()
    ):
        raise HTTPException(422, "Category does not exist in the selected app.")
    return identifier


async def bind_assets(db, document, cover_id):
    document = copy.deepcopy(document)
    media = [node for node in document_nodes(document) if node["type"] in MEDIA_TYPES]
    ids = {node.get("attrs", {}).get("asset_id") for node in media} - {None}
    if cover_id is not None:
        ids.add(cover_id)
    # Official Tiptap nodes store src, not a custom asset_id attribute. Resolve
    # their exact URL in this app DB and verify MIME before accepting the post.
    urls = {
        node.get("attrs", {}).get("src") for node in media if node["type"] in {"image", "audio"}
    }
    rows = {}
    if ids or urls:
        rows = {
            row["id"]: dict(row)
            for row in (
                await db.execute(
                    sa.select(assets).where(
                        sa.or_(assets.c.id.in_(ids), assets.c.public_url.in_(urls))
                    )
                )
            )
            .mappings()
            .all()
        }
    if ids - rows.keys():
        raise HTTPException(422, "Media asset does not exist in the selected app.")
    for row in rows.values():
        try:
            safe_url(row["public_url"])
            if urlsplit(row["public_url"]).query or urlsplit(row["public_url"]).fragment:
                raise ValueError("Not a stable asset URL")
        except ValueError:
            raise HTTPException(422, "Media asset needs a stable public HTTPS URL.") from None
    by_url = {row["public_url"]: row for row in rows.values()}
    for node in media:
        attrs = node.setdefault("attrs", {})
        identifier = attrs.get("asset_id")
        if node["type"] in {"image", "audio"}:
            row = by_url.get(attrs.get("src"))
            if row is None:
                raise HTTPException(
                    422, "Media URL must reference a registered asset in the selected app."
                )
        elif identifier is not None:
            row = rows[identifier]
        else:
            continue
        prefix = {
            "customImage": "image/",
            "customVideo": "video/",
            "customAudio": "audio/",
            "image": "image/",
            "audio": "audio/",
        }.get(node["type"])
        if prefix and not row["media_type"].startswith(prefix):
            raise HTTPException(422, "Media type does not match the document node.")
        attrs["src"] = row["public_url"]
        if node["type"] == "customFileAttachment":
            attrs.update(
                fileName=row["original_filename"],
                fileSize=f"{row['size_bytes']} bytes",
                fileType=row["media_type"],
            )
    if cover_id is not None and not rows[cover_id]["media_type"].startswith("image/"):
        raise HTTPException(422, "The cover must be an image asset.")
    return document, rows[cover_id]["public_url"] if cover_id is not None else None


async def author_snapshot(central, membership):
    owner = await central.get(User, membership.user_id)
    name = ((owner.name or "").strip() if owner else "") or membership.name.strip()
    try:
        snapshot = {
            "author_name": clean_text(name, 160),
            "author_designation": clean_text(membership.designation, 160, empty=True) or None
            if membership.designation is not None
            else None,
            "author_description": description_text(membership.description) or None
            if membership.description is not None
            else None,
            "author_avatar_url": None,
        }
    except ValueError:
        raise HTTPException(
            422,
            "Set a valid profile display name, designation and description before creating a blog.",
        ) from None
    avatar = membership.avatar_url or (owner.avatar_url if owner else None)
    if avatar:
        try:
            snapshot["author_avatar_url"] = safe_url(avatar)
        except ValueError:
            pass
    return snapshot


def finalize(target, values, cover_url):
    if values["status"] == "published":
        if (
            target.public_blog_url
            and "/en/" in urlsplit(target.public_blog_url).path
            and values["locale"] != "en"
        ):
            raise HTTPException(
                409, "Configure this locale's confirmed public blog URL before publishing."
            )
        if not target.public_blog_url:
            raise HTTPException(
                409, "Configure the selected app's confirmed public blog URL before publishing."
            )
        if not plain_text(values["content_json"]).strip() and not any(
            node["type"] in MEDIA_TYPES for node in document_nodes(values["content_json"])
        ):
            raise HTTPException(422, "Add article content before publishing.")
        if values["published_at"] is None:
            values["published_at"] = values["updated_at"]
    values["metadata_json"] = build_metadata(target, values, cover_url)
    return values


async def create_post(db, target, body, snapshot):
    values = body.model_dump(exclude_unset=True)
    cover_details = values.pop("cover_image", None)
    name = values.pop("category_name", None)
    values["category_id"] = await category_id(db, {**values, "category_name": name})
    values.setdefault("locale", body.locale)
    values.setdefault("status", body.status)
    values["excerpt"] = (
        body.excerpt if body.excerpt is not None else clip(plain_text(body.content_json), 280)
    )
    values.setdefault("cover_asset_id", None)
    apply_cover_details(values, cover_details)
    values["content_json"], cover_url = await bind_assets(
        db, values["content_json"], values["cover_asset_id"]
    )
    values.update(snapshot)
    now = utcnow()
    values.update(
        version=1, content_schema_version=1, created_at=now, updated_at=now, published_at=None
    )
    base = slug_base(values["title"])
    for attempt in range(8):
        values["slug"] = base if attempt == 0 else f"{base}-{uuid.uuid4().hex[:8]}"
        finalize(target, values, cover_url)
        try:
            async with db.begin_nested():
                row = (
                    (await db.execute(posts.insert().values(**values).returning(*posts.c)))
                    .mappings()
                    .one()
                )
            return dict(row)
        except IntegrityError:
            if not (
                await db.execute(
                    sa.select(posts.c.id).where(
                        posts.c.locale == values["locale"], posts.c.slug == values["slug"]
                    )
                )
            ).first():
                raise
    raise HTTPException(409, "Post slug could not be allocated. Retry the operation.")


async def update_post(db, target, identifier, body, existing):
    if existing["version"] != body.version:
        raise HTTPException(409, "This post changed. Reload it before saving.")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    cover_details = changes.pop("cover_image", ...)
    name = changes.pop("category_name", None)
    if (
        existing["published_at"] is not None
        and "locale" in changes
        and changes["locale"] != existing["locale"]
    ):
        raise HTTPException(409, "A previously published post's locale cannot be changed.")
    values = {**existing, **changes}
    apply_cover_details(values, cover_details)
    if name is not None or "category_id" in changes:
        values["category_id"] = await category_id(db, {**changes, "category_name": name})
    values["content_json"], cover_url = await bind_assets(
        db, values["content_json"], values["cover_asset_id"]
    )
    values["version"] = existing["version"] + 1
    values["updated_at"] = utcnow()
    base = slug_base(values["title"])
    stable_slug = existing["published_at"] is not None or not ({"title", "locale"} & changes.keys())
    for attempt in range(8):
        values["slug"] = (
            existing["slug"]
            if stable_slug
            else base
            if attempt == 0
            else f"{base}-{uuid.uuid4().hex[:8]}"
        )
        finalize(target, values, cover_url)
        try:
            async with db.begin_nested():
                row = (
                    (
                        await db.execute(
                            posts.update()
                            .where(posts.c.id == identifier, posts.c.version == body.version)
                            .values(**{key: value for key, value in values.items() if key != "id"})
                            .returning(*posts.c)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                raise HTTPException(409, "This post changed. Reload it before saving.")
            return dict(row)
        except IntegrityError:
            if (
                stable_slug
                or not (
                    await db.execute(
                        sa.select(posts.c.id).where(
                            posts.c.locale == values["locale"],
                            posts.c.slug == values["slug"],
                            posts.c.id != identifier,
                        )
                    )
                ).first()
            ):
                raise
    raise HTTPException(409, "Post slug could not be allocated. Retry the operation.")


async def record_audit(central, membership, action, target):
    # The app transaction is already committed. Audit failure must not turn success into a retry.
    try:
        await audit(central, membership, action, target)
        await central.commit()
        return True
    except Exception as error:
        with contextlib.suppress(Exception):
            await central.rollback()
        logger.warning(
            "Blog write committed; central audit unavailable error_type=%s", type(error).__name__
        )
        return False
