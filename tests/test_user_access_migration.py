"""Additive migration preserves unknown ownership and refuses unsafe downgrade."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_access_upgrade_preserves_legacy_rows():
    path = Path(__file__).parents[1] / "alembic/versions/f820b1c943ae_user_access_finalization.py"
    spec = importlib.util.spec_from_file_location("access_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for table in (
            "users",
            "memberships",
            "authorized_apps",
            "access_apikey",
            "service_credentials",
            "extracted_media",
        ):
            connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
            connection.execute(text(f"INSERT INTO {table} (id) VALUES (1)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(text("SELECT session_version FROM users")).scalar_one() == 0
        connection.execute(text("UPDATE users SET session_version=1"))
        with pytest.raises(RuntimeError, match="Reconcile"):
            migration.downgrade()
        connection.execute(text("UPDATE users SET session_version=0"))
        assert connection.execute(text("SELECT is_internal FROM access_apikey")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT authorized_app_id FROM extracted_media")).scalar_one()
            is None
        )
        assert (
            connection.execute(text("SELECT membership_id FROM service_credentials")).scalar_one()
            is None
        )
        connection.execute(text("UPDATE extracted_media SET authorized_app_id=1"))
        with pytest.raises(RuntimeError, match="Reconcile"):
            migration.downgrade()
        connection.execute(text("UPDATE extracted_media SET authorized_app_id=NULL"))
        migration.downgrade()
        assert {c["name"] for c in inspect(connection).get_columns("extracted_media")} == {"id"}
    engine.dispose()
