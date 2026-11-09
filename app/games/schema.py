"""Typed PostgreSQL schema for BondsGame game records and comments."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData(schema="public")

games = sa.Table(
    "games",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("slug", sa.String(128), nullable=False, unique=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("subtitle", sa.Text()),
    sa.Column("excerpt", sa.Text(), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("cover_image", sa.Text(), nullable=False),
    sa.Column("hero_image", sa.Text()),
    sa.Column("developer", sa.String(128)),
    sa.Column("publisher", sa.String(128)),
    sa.Column("genres", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
    sa.Column("platforms", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
    sa.Column("release_date", sa.Date()),
    sa.Column("release_window", sa.String(64)),
    sa.Column("release_status", sa.String(32), nullable=False, server_default="released"),
    sa.Column("editorial_score", sa.Numeric(3, 1)),
    sa.Column("metacritic_score", sa.SmallInteger()),
    sa.Column("verdict", JSONB),
    sa.Column("specs", JSONB),
    sa.Column("purchase_links", JSONB),
    sa.Column("publish_status", sa.String(16), nullable=False, server_default="draft"),
    sa.Column("trending", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("release_status IN ('released', 'upcoming', 'early_access')", name="ck_games_release_status"),
    sa.CheckConstraint("publish_status IN ('draft', 'published')", name="ck_games_publish_status"),
    sa.CheckConstraint("editorial_score IS NULL OR editorial_score BETWEEN 0 AND 10", name="ck_games_editorial_score"),
    sa.CheckConstraint("metacritic_score IS NULL OR metacritic_score BETWEEN 0 AND 100", name="ck_games_metacritic_score"),
    sa.CheckConstraint("jsonb_typeof(verdict) = 'object' OR verdict IS NULL", name="ck_games_verdict_object"),
    sa.CheckConstraint("jsonb_typeof(specs) = 'object' OR specs IS NULL", name="ck_games_specs_object"),
    sa.CheckConstraint("jsonb_typeof(purchase_links) = 'object' OR purchase_links IS NULL", name="ck_games_purchase_links_object"),
)

similar_games_index = sa.Index("ix_games_genres_gin", games.c.genres, postgresql_using="gin")
published_games_index = sa.Index("ix_games_published_updated", games.c.publish_status, games.c.updated_at.desc())

game_comments = sa.Table(
    "game_comments",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("game_id", sa.String(64), sa.ForeignKey("public.games.id", ondelete="CASCADE"), nullable=False),
    sa.Column("author_name", sa.String(128), nullable=False),
    sa.Column("author_avatar", sa.Text()),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("upvotes", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("downvotes", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("moderation_status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("upvotes >= 0", name="ck_game_comments_upvotes_nonnegative"),
    sa.CheckConstraint("downvotes >= 0", name="ck_game_comments_downvotes_nonnegative"),
    sa.CheckConstraint("moderation_status IN ('pending', 'approved', 'rejected')", name="ck_game_comments_moderation_status"),
    sa.CheckConstraint("length(trim(author_name)) BETWEEN 1 AND 128", name="ck_game_comments_author_name"),
    sa.CheckConstraint("length(trim(content)) BETWEEN 1 AND 5000", name="ck_game_comments_content"),
)

comments_by_game_index = sa.Index(
    "ix_game_comments_game_created", game_comments.c.game_id, game_comments.c.created_at.desc(), game_comments.c.id.desc()
)
