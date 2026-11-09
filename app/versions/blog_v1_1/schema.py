"""Additive V1.1 post schema; the original V1 schema and marker remain unchanged."""

import sqlalchemy as sa
from app.blog.migrations.schema_v3 import build_metadata
from sqlalchemy.dialects.postgresql import JSONB

metadata = build_metadata("public")
posts = metadata.tables["public.blog_posts"]
posts.append_column(
    sa.Column(
        "blog_details_json",
        sa.JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        server_default=sa.text("'{}'"),
    )
)
