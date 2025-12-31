"""Consolidate Sigenergy curtailment - remove separate flag, add state tracking

Move DC curtailment control to solar_curtailment_enabled (shared with Tesla).
Add state tracking columns for Sigenergy curtailment.

Revision ID: x7q8r9s0t1u2
Revises: w6p7q8r9s0t1
Create Date: 2024-12-31 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'x7q8r9s0t1u2'
down_revision = 'w6p7q8r9s0t1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        # Remove the separate Sigenergy DC curtailment flag
        # (now using solar_curtailment_enabled for both Tesla and Sigenergy)
        batch_op.drop_column('sigenergy_dc_curtailment_enabled')

        # Add Sigenergy curtailment state tracking
        batch_op.add_column(sa.Column('sigenergy_curtailment_state', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('sigenergy_curtailment_updated', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        # Remove new state tracking columns
        batch_op.drop_column('sigenergy_curtailment_updated')
        batch_op.drop_column('sigenergy_curtailment_state')

        # Restore the separate DC curtailment flag
        batch_op.add_column(sa.Column('sigenergy_dc_curtailment_enabled', sa.Boolean(), nullable=True, server_default='0'))
