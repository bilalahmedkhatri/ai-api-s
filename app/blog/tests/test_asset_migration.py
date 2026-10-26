import pytest
import sqlalchemy as sa
from alembic import command
from app.blog.migrate import migration_config, upgrade_schema, verify_schema


def test_v1_upgrade_preserves_legacy_assets_and_does_not_assign_owners():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as db:
        command.upgrade(migration_config(db), "blog_0001")
        db.execute(
            sa.text(
                "INSERT INTO blog_assets(object_key,public_url,original_filename,media_type,size_bytes) VALUES ('old','https://media.test/old.png','old.png','image/png',1)"
            )
        )
        assert upgrade_schema(db)["revision"] == "blog_0003"
        assert (
            db.execute(sa.text("SELECT uploaded_by_membership_id FROM blog_assets")).scalar_one()
            is None
        )
        assert verify_schema(db)["rows"]["blog_assets"] == 1
        with pytest.raises(RuntimeError, match="Blog data exists"):
            command.downgrade(migration_config(db), "blog_0001")
    engine.dispose()
