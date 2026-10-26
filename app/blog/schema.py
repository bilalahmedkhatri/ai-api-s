"""Current public blog tables, separate from the gateway ORM Base."""

from app.blog.migrations.schema_v3 import build_metadata

metadata = build_metadata("public")
blog_categories = metadata.tables["public.blog_categories"]
blog_assets = metadata.tables["public.blog_assets"]
blog_posts = metadata.tables["public.blog_posts"]
__all__ = ["metadata", "blog_assets", "blog_categories", "blog_posts"]
