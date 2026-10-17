"""Scope query request records to their authenticated app; legacy rows stay unassigned."""

import json

import sqlalchemy as sa
from alembic import op

revision = "d920f41a672e"
down_revision = "c84f10d2a639"
branch_labels = None
depends_on = None

READ_GRANTS = ["GET /api/v1/query/requests", "GET /api/v1/query/requests/{query_id}"]


def upgrade():
    with op.batch_alter_table("queries") as batch:
        batch.add_column(sa.Column("authorized_app_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_query_authorized_app", "authorized_apps", ["authorized_app_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_queries_authorized_app_id", ["authorized_app_id"])
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, allowed_endpoints_json FROM authorized_apps")).mappings():
        try:
            grants = json.loads(row["allowed_endpoints_json"] or "[]")
        except (ValueError, TypeError):
            continue
        if (isinstance(grants, list) and all(isinstance(grant, str) for grant in grants)
                and "POST /api/v1/query/" in grants):
            grants = list(dict.fromkeys(grants + READ_GRANTS))
            bind.execute(sa.text("UPDATE authorized_apps SET allowed_endpoints_json=:grants WHERE id=:id"),
                         {"grants": json.dumps(grants), "id": row["id"]})


def downgrade():
    with op.batch_alter_table("queries") as batch:
        batch.drop_index("ix_queries_authorized_app_id")
        batch.drop_constraint("fk_query_authorized_app", type_="foreignkey")
        batch.drop_column("authorized_app_id")
