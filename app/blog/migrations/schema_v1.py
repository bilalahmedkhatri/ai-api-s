"""Frozen v1 table definitions. New changes require a new migration."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def build_metadata(schema=None):
    metadata = sa.MetaData(schema=schema)
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    prefix = f"{schema}." if schema else ""
    sa.Table(
        "blog_categories",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("normalized_name", name="uq_blog_categories_normalized_name"),
        sa.UniqueConstraint("slug", name="uq_blog_categories_slug"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_blog_categories_name"),
        sa.CheckConstraint(
            "length(trim(normalized_name)) > 0", name="ck_blog_categories_normalized_name"
        ),
        sa.CheckConstraint("length(trim(slug)) > 0", name="ck_blog_categories_slug"),
    )
    sa.Table(
        "blog_assets",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("public_url", sa.Text, nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(127), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("alt_text", sa.Text, nullable=False, server_default=""),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("object_key", name="uq_blog_assets_object_key"),
        sa.CheckConstraint("length(trim(object_key)) > 0", name="ck_blog_assets_object_key"),
        sa.CheckConstraint("public_url LIKE 'https://%'", name="ck_blog_assets_public_url"),
        sa.CheckConstraint("length(trim(original_filename)) > 0", name="ck_blog_assets_filename"),
        sa.CheckConstraint("length(trim(media_type)) > 0", name="ck_blog_assets_media_type"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_blog_assets_size"),
        sa.CheckConstraint("width IS NULL OR width > 0", name="ck_blog_assets_width"),
        sa.CheckConstraint("height IS NULL OR height > 0", name="ck_blog_assets_height"),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="ck_blog_assets_duration"
        ),
    )
    posts = sa.Table(
        "blog_posts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("locale", sa.String(35), nullable=False, server_default="en"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "category_id",
            sa.Integer,
            sa.ForeignKey(prefix + "blog_categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("content_json", json_type, nullable=False),
        sa.Column("content_schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("metadata_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("author_name", sa.String(160), nullable=False),
        sa.Column("author_designation", sa.String(160), nullable=True),
        sa.Column("author_avatar_url", sa.Text, nullable=True),
        sa.Column(
            "cover_asset_id",
            sa.Integer,
            sa.ForeignKey(prefix + "blog_assets.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("locale", "slug", name="uq_blog_posts_locale_slug"),
        sa.CheckConstraint("length(trim(locale)) > 0", name="ck_blog_posts_locale"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_blog_posts_title"),
        sa.CheckConstraint("length(trim(slug)) > 0", name="ck_blog_posts_slug"),
        sa.CheckConstraint("length(trim(author_name)) > 0", name="ck_blog_posts_author"),
        sa.CheckConstraint("content_schema_version > 0", name="ck_blog_posts_content_version"),
        sa.CheckConstraint("version > 0", name="ck_blog_posts_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_blog_posts_status"
        ),
        sa.CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL", name="ck_blog_posts_publication"
        ),
        sa.CheckConstraint(
            "COALESCE(jsonb_typeof(content_json) = 'object' AND content_json->>'type' = 'doc' "
            "AND jsonb_typeof(content_json->'content') = 'array', false)",
            name="ck_blog_posts_document",
        ).ddl_if(dialect="postgresql"),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'", name="ck_blog_posts_metadata"
        ).ddl_if(dialect="postgresql"),
    )
    sa.Index("ix_blog_posts_status_published_at", posts.c.status, posts.c.published_at)
    sa.Index("ix_blog_posts_category_status", posts.c.category_id, posts.c.status)
    return metadata


metadata = build_metadata()
blog_categories = metadata.tables["blog_categories"]
blog_assets = metadata.tables["blog_assets"]
blog_posts = metadata.tables["blog_posts"]
