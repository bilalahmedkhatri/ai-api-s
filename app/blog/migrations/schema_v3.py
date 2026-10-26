"""Author biography snapshot; existing posts remain unassigned."""

import sqlalchemy as sa

from app.blog.migrations.schema_v2 import build_metadata as build_v2

REVISION = "blog_0003"


def build_metadata(schema=None):
    metadata = build_v2(schema)
    posts = metadata.tables[(schema + "." if schema else "") + "blog_posts"]
    posts.append_column(sa.Column("author_description", sa.Text, nullable=True))
    return metadata
