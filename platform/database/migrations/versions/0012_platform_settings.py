"""platform_settings table (Configuration Studio)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_settings',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('live_weather_enabled', sa.Boolean(), nullable=False),
        sa.Column('weather_site_lat', sa.Float(), nullable=True),
        sa.Column('weather_site_lon', sa.Float(), nullable=True),
        sa.Column('gateway_circuit_breaker_enabled', sa.Boolean(), nullable=False),
        sa.Column('gateway_timeout_seconds', sa.Float(), nullable=False),
        sa.Column('gateway_failure_threshold', sa.Integer(), nullable=False),
        sa.Column('gateway_cooldown_seconds', sa.Integer(), nullable=False),
        sa.Column('sso_enabled', sa.Boolean(), nullable=False),
        sa.Column('updated_by', sa.String(length=200), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', name='uq_platform_settings_tenant'),
    )
    op.create_index(op.f('ix_platform_settings_tenant_id'), 'platform_settings', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_platform_settings_tenant_id'), table_name='platform_settings')
    op.drop_table('platform_settings')
