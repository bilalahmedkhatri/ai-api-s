"""SQLite migration preserves members and refuses to orphan active projects."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_customer_key_upgrade_downgrade():
    path = Path(__file__).parents[1] / "alembic/versions/ab92d18e740c_customer_api_keys.py"
    spec = importlib.util.spec_from_file_location("customer_key_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE authorized_apps (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE memberships (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        connection.execute(text("INSERT INTO memberships VALUES (1, 'keep@example.test')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(text("SELECT email, api_keys_unlocked, api_project_id FROM memberships")).one() == ("keep@example.test", 0, None)
        assert {"master_key_encrypted", "api_keys_unlocked", "api_project_id"} <= {c["name"] for c in inspect(connection).get_columns("memberships")}
        connection.execute(text("INSERT INTO authorized_apps VALUES (7)"))
        connection.execute(text("UPDATE memberships SET api_project_id=7"))
        with pytest.raises(RuntimeError, match="Reconcile"):
            migration.downgrade()
        connection.execute(text("UPDATE memberships SET api_project_id=NULL"))
        migration.downgrade()
        assert connection.execute(text("SELECT email FROM memberships")).scalar_one() == "keep@example.test"
    engine.dispose()
