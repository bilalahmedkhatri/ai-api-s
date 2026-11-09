"""add url_address to prompts

Revision ID: 8a75330cbf08
Revises: 61addd781029
Create Date: 2026-11-08 03:49:47.830462

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a75330cbf08'
down_revision: Union[str, Sequence[str], None] = '61addd781029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prompts",
        sa.Column("url_address", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prompts", "url_address")
