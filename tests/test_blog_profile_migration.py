"""Disposable schema tests; no configured database access."""

import pytest
import sqlalchemy as sa
from app.blog.migrate import BlogMigrationError
from app.blog.profile_migration import PREVIOUS, REVISION, upgrade


def test_additive_profile_upgrade_preserves_existing_records_and_is_repeatable():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE memberships (id INTEGER PRIMARY KEY, name VARCHAR(80), designation VARCHAR(160))"
        )
        connection.exec_driver_sql(
            "INSERT INTO memberships VALUES (1, 'Existing Writer', 'Editor')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        )
        connection.execute(
            sa.text("INSERT INTO alembic_version VALUES (:parent)"), {"parent": PREVIOUS}
        )
        assert upgrade(connection)["revision"] == REVISION
        row = connection.execute(sa.text("SELECT * FROM memberships")).mappings().one()
        assert row["name"] == "Existing Writer" and row["designation"] == "Editor"
        assert row["avatar_url"] is None and row["description"] is None
        assert upgrade(connection)["status"] == "already_current"
    engine.dispose()


def test_unexpected_parent_fails_without_changing_columns():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memberships (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('unknown')")
        with pytest.raises(BlogMigrationError):
            upgrade(connection)
        assert [item["name"] for item in sa.inspect(connection).get_columns("memberships")] == [
            "id"
        ]
    engine.dispose()
