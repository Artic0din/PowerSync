"""Add network distributor and tariff code fields

Revision ID: h1a2b3c4d5e6
Revises: g9b4d8f03e25
Create Date: 2025-12-14

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h1a2b3c4d5e6'
down_revision = 'g9b4d8f03e25'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns for aemo_to_tariff library integration
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('network_distributor', sa.String(20), server_default='energex'))
        batch_op.add_column(sa.Column('network_tariff_code', sa.String(20), server_default='NTC6900'))
        batch_op.add_column(sa.Column('network_use_manual_rates', sa.Boolean(), server_default='0'))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('network_use_manual_rates')
        batch_op.drop_column('network_tariff_code')
        batch_op.drop_column('network_distributor')
