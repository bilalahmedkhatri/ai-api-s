"""Isolated schema, rollback and preservation tests. Never use configured databases."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from app.blog.migrate import (
    BlogMigrationError,
    current_revision,
    migration_config,
    render_sql,
    upgrade_schema,
    verify_schema,
)
from app.blog.migrations.schema_v3 import build_metadata
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def db():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(
            sa.text("CREATE TABLE website_data (id INTEGER PRIMARY KEY, value TEXT)")
        )
        connection.execute(sa.text("INSERT INTO website_data VALUES (1, 'preserved')"))
        connection.execute(
            sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        connection.execute(sa.text("INSERT INTO alembic_version VALUES ('website_revision')"))
        yield connection
    engine.dispose()


def post(**overrides):
    return {
        "locale": "en",
        "title": "An article",
        "slug": "an-article",
        "content_json": {"type": "doc", "content": [{"type": "paragraph"}]},
        "author_name": "Existing dashboard user",
        "author_designation": "Editor",
        **overrides,
    }


def assert_rejected(db, statement):
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(statement)


def test_upgrade_and_idempotence_preserve_unrelated_data_and_migration_history(db):
    result = upgrade_schema(db)
    assert result["status"] == "upgraded"
    assert result["rows"] == {"blog_assets": 0, "blog_categories": 0, "blog_posts": 0}
    assert upgrade_schema(db)["status"] == "already_current"
    assert db.execute(sa.text("SELECT value FROM website_data")).scalar_one() == "preserved"
    assert (
        db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        == "website_revision"
    )
    assert current_revision(db) == "blog_0003"


def test_blog_schema_is_separate_from_central_identity_tables():
    metadata = build_metadata()
    assert set(metadata.tables) == {"blog_posts", "blog_categories", "blog_assets"}
    columns = set(metadata.tables["blog_posts"].c.keys())
    assert not columns & {
        "user_id",
        "author_id",
        "membership_id",
        "authorized_app_id",
        "content_html",
    }
    for table in metadata.tables.values():
        assert all(fk.column.table.name in metadata.tables for fk in table.foreign_keys)


def test_json_roundtrip_author_snapshot_and_locale_slug_uniqueness(db):
    upgrade_schema(db)
    posts = build_metadata().tables["blog_posts"]
    db.execute(posts.insert().values(**post()))
    stored = db.execute(sa.select(posts)).mappings().one()
    assert stored["content_json"]["type"] == "doc"
    assert stored["metadata_json"] == {}
    assert stored["author_designation"] == "Editor"
    assert stored["status"] == "draft" and stored["version"] == 1
    assert stored["created_at"] and stored["updated_at"]
    assert_rejected(db, posts.insert().values(**post()))
    db.execute(posts.insert().values(**post(locale="ar")))
    assert verify_schema(db)["rows"]["blog_posts"] == 2


def test_categories_unique_and_foreign_keys_remain_local_and_restrict_deletion(db):
    upgrade_schema(db)
    metadata = build_metadata()
    cats, posts = metadata.tables["blog_categories"], metadata.tables["blog_posts"]
    db.execute(
        cats.insert().values(id=7, name="AI Tools", normalized_name="ai tools", slug="ai-tools")
    )
    assert_rejected(
        db, cats.insert().values(name="AI tools", normalized_name="ai tools", slug="other")
    )
    assert_rejected(db, posts.insert().values(**post(category_id=999)))
    assert_rejected(db, posts.insert().values(**post(cover_asset_id=999)))
    db.execute(posts.insert().values(**post(category_id=7)))
    assert_rejected(db, cats.delete().where(cats.c.id == 7))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "deleted"},
        {"status": "published"},
        {"version": 0},
        {"content_schema_version": 0},
        {"title": " "},
        {"author_name": " "},
    ],
)
def test_post_constraints_reject_invalid_states(db, changes):
    upgrade_schema(db)
    posts = build_metadata().tables["blog_posts"]
    assert_rejected(db, posts.insert().values(**post(**changes)))


def test_assets_validate_size_dimensions_and_public_url(db):
    upgrade_schema(db)
    assets = build_metadata().tables["blog_assets"]
    valid = {
        "object_key": "blogs/azeemlab/asset.png",
        "public_url": "https://media.example.test/asset.png",
        "original_filename": "asset.png",
        "media_type": "image/png",
        "size_bytes": 40,
    }
    for changes in (
        {"size_bytes": -1},
        {"width": 0},
        {"duration_seconds": -1},
        {"public_url": "javascript:bad"},
    ):
        assert_rejected(db, assets.insert().values(**{**valid, **changes}))
    db.execute(assets.insert().values(**valid))
    assert_rejected(db, assets.insert().values(**valid))


def test_unmanaged_existing_blog_table_is_never_adopted_or_overwritten(db):
    db.execute(sa.text("CREATE TABLE blog_posts (id INTEGER PRIMARY KEY)"))
    with pytest.raises(BlogMigrationError, match="already exist"):
        upgrade_schema(db)
    assert set(sa.inspect(db).get_table_names()) == {
        "website_data",
        "alembic_version",
        "blog_posts",
    }


def test_schema_drift_is_refused_even_with_current_marker(db):
    upgrade_schema(db)
    db.execute(sa.text("ALTER TABLE blog_posts ADD COLUMN unexpected TEXT"))
    with pytest.raises(BlogMigrationError, match="column inventory"):
        upgrade_schema(db)


def test_empty_downgrade_and_reupgrade_preserve_website_tables(db):
    upgrade_schema(db)
    command.downgrade(migration_config(db), "base")
    assert not (
        {"blog_posts", "blog_categories", "blog_assets"} & set(sa.inspect(db).get_table_names())
    )
    assert db.execute(sa.text("SELECT value FROM website_data")).scalar_one() == "preserved"
    assert (
        db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        == "website_revision"
    )
    assert upgrade_schema(db)["status"] == "upgraded"


def test_downgrade_refuses_to_drop_any_recorded_blog_data(db):
    upgrade_schema(db)
    categories = build_metadata().tables["blog_categories"]
    db.execute(categories.insert().values(name="News", normalized_name="news", slug="news"))
    with pytest.raises(RuntimeError, match="Blog data exists"):
        command.downgrade(migration_config(db), "base")
    assert current_revision(db) == "blog_0003"
    assert verify_schema(db)["rows"]["blog_categories"] == 1


def test_postgresql_sql_is_reviewable_and_independent_of_gateway_schema():
    sql = render_sql()
    assert "CREATE TABLE public.blog_schema_version" in sql
    assert "CREATE TABLE public.blog_posts" in sql
    assert "JSONB" in sql and "jsonb_typeof(content_json)" in sql
    assert "REFERENCES public.blog_categories" in sql
    assert "REFERENCES public.blog_assets" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "CREATE TABLE users" not in sql and "CREATE TABLE memberships" not in sql
    assert "CREATE TABLE public.alembic_version" not in sql


def test_central_designation_upgrade_preserves_profiles_and_guards_populated_downgrade():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/b1023c5d7e90_membership_designation.py"
    )
    spec = importlib.util.spec_from_file_location("designation_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE memberships (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(sa.text("INSERT INTO memberships VALUES (1, 'Existing user')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(sa.text("SELECT name, designation FROM memberships")).one() == (
            "Existing user",
            None,
        )
        connection.execute(sa.text("UPDATE memberships SET designation='Writer' WHERE id=1"))
        with pytest.raises(RuntimeError, match="Designation data exists"):
            migration.downgrade()
        connection.execute(sa.text("UPDATE memberships SET designation=NULL"))
        migration.downgrade()
        assert {c["name"] for c in sa.inspect(connection).get_columns("memberships")} == {
            "id",
            "name",
        }
        assert (
            connection.execute(sa.text("SELECT name FROM memberships")).scalar_one()
            == "Existing user"
        )
    engine.dispose()


def test_central_runner_uses_explicit_connection_and_applies_only_designation_revision(db):
    from app.blog.central_migration import upgrade_designation

    db.execute(sa.text("UPDATE alembic_version SET version_num='f820b1c943ae'"))
    db.execute(sa.text("CREATE TABLE memberships (id INTEGER PRIMARY KEY, name TEXT)"))
    db.execute(sa.text("INSERT INTO memberships VALUES (1, 'Unchanged')"))
    assert upgrade_designation(db)["status"] == "upgraded"
    assert upgrade_designation(db)["status"] == "already_current"
    assert db.execute(sa.text("SELECT name, designation FROM memberships")).one() == (
        "Unchanged",
        None,
    )
    assert set(sa.inspect(db).get_table_names()) == {
        "website_data",
        "alembic_version",
        "memberships",
    }


def test_central_runner_refuses_to_apply_unrelated_pending_migrations(db):
    from app.blog.central_migration import upgrade_designation

    db.execute(sa.text("CREATE TABLE memberships (id INTEGER PRIMARY KEY)"))
    with pytest.raises(BlogMigrationError, match="expected parent"):
        upgrade_designation(db)
    assert {item["name"] for item in sa.inspect(db).get_columns("memberships")} == {"id"}
