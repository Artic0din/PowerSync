"""Add manual discharge mode fields

Revision ID: l5e6f7g8h9i0
Revises: k4d5e6f7g8h9
Create Date: 2024-12-18

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'l5e6f7g8h9i0'
down_revision = 'k4d5e6f7g8h9'
branch_labels = None
depends_on = None


def upgrade():
    # Add manual discharge mode fields
    op.add_column('user', sa.Column('manual_discharge_active', sa.Boolean(), nullable=True, server_default='0'))
    op.add_column('user', sa.Column('manual_discharge_expires_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('manual_discharge_saved_tariff_id', sa.Integer(), nullable=True))

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_manual_discharge_saved_tariff',
        'user', 'saved_tou_profile',
        ['manual_discharge_saved_tariff_id'], ['id']
    )


def downgrade():
    # Remove foreign key constraint
    op.drop_constraint('fk_manual_discharge_saved_tariff', 'user', type_='foreignkey')

    # Remove columns
    op.drop_column('user', 'manual_discharge_saved_tariff_id')
    op.drop_column('user', 'manual_discharge_expires_at')
    op.drop_column('user', 'manual_discharge_active')
