"""Additive biography migration preserves posts and earlier schema verification."""

import pytest
import sqlalchemy as sa
from alembic import command
from app.blog.migrate import migration_config, upgrade_schema, verify_schema


def test_v2_upgrade_preserves_posts_and_refuses_lossy_biography_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as db:
        command.upgrade(migration_config(db), "blog_0002")
        db.execute(
            sa.text(
                "INSERT INTO blog_posts(title,slug,content_json,author_name,author_designation) "
                "VALUES ('Legacy','legacy','{}','Original Author','Writer')"
            )
        )
        assert verify_schema(db, "blog_0002")["rows"]["blog_posts"] == 1
        assert upgrade_schema(db)["revision"] == "blog_0003"
        assert db.execute(
            sa.text("SELECT author_name,author_designation,author_description FROM blog_posts")
        ).one() == ("Original Author", "Writer", None)
        db.execute(
            sa.text("UPDATE blog_posts SET author_description=:bio"),
            {"bio": "Writes guides.\nReviews code."},
        )
        assert verify_schema(db)["rows"]["blog_posts"] == 1
        with pytest.raises(RuntimeError, match="author descriptions"):
            command.downgrade(migration_config(db), "blog_0002")
    engine.dispose()
