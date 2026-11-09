"""V1.1 writes use an additive column while reusing V1 business rules."""

import uuid

import sqlalchemy as sa
from app.blog import service as legacy
from app.versions.blog_v1_1.details import bind_details
from app.versions.blog_v1_1.schema import posts
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


async def prepare_details(db, values, body, uploader_id=None):
    supplied = body.blog_details.model_dump() if body.blog_details is not None else None
    if supplied is not None:
        featured = supplied["featured_image"]
        cover = featured["asset_id"] if featured else None
        if "cover_asset_id" in body.model_fields_set and body.cover_asset_id != cover:
            raise HTTPException(422, "Featured image and cover asset must match.")
        if "cover_image" in body.model_fields_set:
            raise HTTPException(422, "Use blog_details or legacy cover_image, not both.")
        values["cover_asset_id"] = cover
    return await bind_details(db, values, supplied, uploader_id=uploader_id)


def finalize(target, values, cover_url):
    legacy.finalize(target, values, cover_url)
    # SEO metadata stays derived; editorial details live only in the new column.
    values["metadata_json"].pop("cover_image", None)
    values["blog_details_json"]["author_image"] = {
        "url": values.get("author_avatar_url"),
        "alt_text": values["author_name"],
    }
    featured = values["blog_details_json"]["featured_image"]
    if featured:
        values["metadata_json"]["open_graph"]["image_alt"] = featured["alt_text"]
        values["metadata_json"]["twitter"]["image_alt"] = featured["alt_text"]


async def create_post(db, target, body, snapshot, *, creator_membership_id=None, uploader_id=None):
    values = body.model_dump(exclude_unset=True, exclude={"blog_details"})
    cover_details = values.pop("cover_image", None)
    name = values.pop("category_name", None)
    values["category_id"] = await legacy.category_id(db, {**values, "category_name": name})
    values.setdefault("locale", body.locale)
    values.setdefault("status", body.status)
    values["excerpt"] = (
        body.excerpt
        if body.excerpt is not None
        else legacy.clip(legacy.plain_text(body.content_json), 280)
    )
    values.setdefault("cover_asset_id", None)
    legacy.apply_cover_details(values, cover_details)
    if creator_membership_id is not None:
        values["metadata_json"]["created_by_membership_id"] = creator_membership_id
    cover_url = await prepare_details(db, values, body, uploader_id)
    values.update(snapshot)
    now = legacy.utcnow()
    values.update(
        version=1, content_schema_version=1, created_at=now, updated_at=now, published_at=None
    )
    base = legacy.slug_base(values["title"])
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


async def update_post(
    db,
    target,
    identifier,
    body,
    existing,
    *,
    author=None,
    uploader_id=None,
    allow_locale_change=False,
):
    if existing["version"] != body.version:
        raise HTTPException(409, "This post changed. Reload it before saving.")
    changes = body.model_dump(exclude_unset=True, exclude={"version", "blog_details"})
    cover_details = changes.pop("cover_image", ...)
    name = changes.pop("category_name", None)
    if (
        not allow_locale_change
        and existing["published_at"] is not None
        and "locale" in changes
        and changes["locale"] != existing["locale"]
    ):
        raise HTTPException(409, "A previously published post's locale cannot be changed.")
    values = {**existing, **changes}
    if author is not None:
        values.update(author)
    legacy.apply_cover_details(values, cover_details)
    if name is not None or "category_id" in changes:
        values["category_id"] = await legacy.category_id(db, {**changes, "category_name": name})
    cover_url = await prepare_details(db, values, body, uploader_id)
    values["version"] = existing["version"] + 1
    values["updated_at"] = legacy.utcnow()
    base = legacy.slug_base(values["title"])
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
            if stable_slug and values["locale"] != existing["locale"]:
                raise HTTPException(
                    409, "This slug is already used in the selected locale."
                ) from None
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
