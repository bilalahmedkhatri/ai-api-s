"""add prompt creator membership"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c42d170b6e1"
down_revision: Union[str, Sequence[str], None] = "8a75330cbf08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prompts",
        sa.Column("created_by_membership_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_prompts_created_by_membership_id",
        "prompts",
        ["created_by_membership_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompts_created_by_membership_id", table_name="prompts")
    op.drop_column("prompts", "created_by_membership_id")
