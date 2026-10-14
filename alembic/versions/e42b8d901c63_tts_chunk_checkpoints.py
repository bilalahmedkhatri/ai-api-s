"""App-owned TTS jobs and indexed durable chunk checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "e42b8d901c63"
down_revision = "c93d5e71ab20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tts_jobs") as batch:
        batch.add_column(sa.Column("authorized_app_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("generation_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("run_token", sa.String(64), nullable=True))
        batch.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_tts_job_app", "authorized_apps", ["authorized_app_id"], ["id"])
        batch.create_index("ix_tts_jobs_authorized_app_id", ["authorized_app_id"])
    op.create_table(
        "tts_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("tts_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(512)),
        sa.Column("error_message", sa.String(500)),
        sa.UniqueConstraint("job_id", "chunk_index", name="uq_tts_chunk_index"),
    )
    op.create_index("ix_tts_chunks_job_id", "tts_chunks", ["job_id"])


def downgrade():
    op.drop_table("tts_chunks")
    with op.batch_alter_table("tts_jobs") as batch:
        batch.drop_index("ix_tts_jobs_authorized_app_id")
        batch.drop_constraint("fk_tts_job_app", type_="foreignkey")
        for column in ("authorized_app_id", "generation_json", "run_token", "lease_until"):
            batch.drop_column(column)
