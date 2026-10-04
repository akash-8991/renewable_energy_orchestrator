"""forecast criteria on platform_settings + forecast_models table

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('platform_settings', sa.Column('forecast_criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        'forecast_models',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('asset_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('variable', sa.String(length=20), nullable=False),
        sa.Column('algorithm', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('n_samples', sa.Integer(), nullable=False),
        sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('trained_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'asset_id', 'variable', name='uq_forecast_model_asset_variable'),
    )
    op.create_index(op.f('ix_forecast_models_tenant_id'), 'forecast_models', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_forecast_models_asset_id'), 'forecast_models', ['asset_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_forecast_models_asset_id'), table_name='forecast_models')
    op.drop_index(op.f('ix_forecast_models_tenant_id'), table_name='forecast_models')
    op.drop_table('forecast_models')
    op.drop_column('platform_settings', 'forecast_criteria')
