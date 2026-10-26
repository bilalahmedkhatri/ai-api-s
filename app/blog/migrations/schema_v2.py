"""Current schema adds uploader attribution without cross-database foreign keys."""

import sqlalchemy as sa

from app.blog.migrations.schema_v1 import build_metadata as build_v1

REVISION = "blog_0002"


def build_metadata(schema=None):
    metadata = build_v1(schema)
    assets = metadata.tables[(schema + "." if schema else "") + "blog_assets"]
    assets.append_column(sa.Column("uploaded_by_membership_id", sa.Integer, nullable=True))
    sa.Index("ix_blog_assets_uploader_id", assets.c.uploaded_by_membership_id, assets.c.id)
    return metadata
