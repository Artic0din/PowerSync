"""Add Chip Mode fields

Revision ID: t3m4n5o6p7q8
Revises: s2l3m4n5o6p7
Create Date: 2025-12-31

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 't3m4n5o6p7q8'
down_revision = 's2l3m4n5o6p7'
branch_labels = None
depends_on = None


def upgrade():
    # Add Chip Mode columns
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chip_mode_enabled', sa.Boolean(), nullable=True, default=False))
        batch_op.add_column(sa.Column('chip_mode_start', sa.String(5), nullable=True, default='22:00'))
        batch_op.add_column(sa.Column('chip_mode_end', sa.String(5), nullable=True, default='06:00'))
        batch_op.add_column(sa.Column('chip_mode_threshold', sa.Float(), nullable=True, default=30.0))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('chip_mode_threshold')
        batch_op.drop_column('chip_mode_end')
        batch_op.drop_column('chip_mode_start')
        batch_op.drop_column('chip_mode_enabled')
