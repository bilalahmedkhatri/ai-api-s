"""Server-owned asset listing: ordinary members and administrators see only their uploads."""

import sqlalchemy as sa

from app.blog.schema import blog_assets


async def list_assets(db, membership_id, *, page, page_size, media_type=None):
    conditions = [blog_assets.c.uploaded_by_membership_id == membership_id]
    if media_type == "image":
        conditions.append(blog_assets.c.media_type.startswith("image/"))
    total = (
        await db.execute(sa.select(sa.func.count()).select_from(blog_assets).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                sa.select(blog_assets)
                .where(*conditions)
                .order_by(blog_assets.c.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "assets": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
