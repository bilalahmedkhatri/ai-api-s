"""Frame counts allow ordered previews after temporary chunk deletion."""
import sqlalchemy as sa
from alembic import op

revision = 'f53c9e012d74'
down_revision = 'e42b8d901c63'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tts_chunks', sa.Column('frame_count', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('tts_chunks', 'frame_count')
