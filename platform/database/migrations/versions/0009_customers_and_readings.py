"""retail customers (profile + daily consumption/generation readings)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'customers',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('customer_ref', sa.String(length=40), nullable=False),
        sa.Column('customer_type', sa.String(length=30), nullable=False),
        sa.Column('region', sa.String(length=40), nullable=False),
        sa.Column('annual_consumption_kwh', sa.Float(), nullable=False),
        sa.Column('renewable_profile', sa.String(length=30), nullable=True),
        sa.Column('solar_capacity_kw', sa.Float(), nullable=False),
        sa.Column('wind_capacity_kw', sa.Float(), nullable=False),
        sa.Column('battery_installed', sa.Boolean(), nullable=False),
        sa.Column('battery_capacity_kwh', sa.Float(), nullable=False),
        sa.Column('tariff_plan', sa.String(length=60), nullable=True),
        sa.Column('standing_charge_gbp_day', sa.Float(), nullable=True),
        sa.Column('base_rate_gbp_kwh', sa.Float(), nullable=True),
        sa.Column('offpeak_rate_gbp_kwh', sa.Float(), nullable=True),
        sa.Column('occupants', sa.Float(), nullable=True),
        sa.Column('property_size_m2', sa.Float(), nullable=True),
        sa.Column('business_size', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'customer_ref', name='uq_customer_ref'),
    )
    op.create_index(op.f('ix_customers_tenant_id'), 'customers', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_customers_customer_ref'), 'customers', ['customer_ref'], unique=False)
    op.create_index(op.f('ix_customers_customer_type'), 'customers', ['customer_type'], unique=False)
    op.create_index(op.f('ix_customers_region'), 'customers', ['region'], unique=False)

    # Composite PK (id, event_time): event_time must be part of every
    # unique/primary key on a Timescale hypertable's partitioning column —
    # same reasoning as telemetry (migration 0003).
    op.create_table(
        'customer_readings',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('customer_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumption_kwh', sa.Float(), nullable=False),
        sa.Column('solar_generation_kwh', sa.Float(), nullable=False),
        sa.Column('wind_generation_kwh', sa.Float(), nullable=False),
        sa.Column('net_grid_import_kwh', sa.Float(), nullable=False),
        sa.Column('export_kwh', sa.Float(), nullable=False),
        sa.Column('avg_energy_rate_gbp_kwh', sa.Float(), nullable=False),
        sa.Column('estimated_cost_gbp', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id', 'event_time'),
        sa.UniqueConstraint('tenant_id', 'customer_id', 'event_time', name='uq_customer_reading'),
    )
    op.create_index(op.f('ix_customer_readings_tenant_id'), 'customer_readings', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_customer_readings_customer_id'), 'customer_readings', ['customer_id'], unique=False)
    op.create_index(op.f('ix_customer_readings_event_time'), 'customer_readings', ['event_time'], unique=False)
    op.create_index('ix_customer_readings_customer_time', 'customer_readings', ['customer_id', 'event_time'], unique=False)

    op.execute(
        "SELECT create_hypertable('customer_readings', 'event_time', if_not_exists => TRUE, migrate_data => TRUE);"
    )


def downgrade() -> None:
    op.drop_index('ix_customer_readings_customer_time', table_name='customer_readings')
    op.drop_index(op.f('ix_customer_readings_event_time'), table_name='customer_readings')
    op.drop_index(op.f('ix_customer_readings_customer_id'), table_name='customer_readings')
    op.drop_index(op.f('ix_customer_readings_tenant_id'), table_name='customer_readings')
    op.drop_table('customer_readings')

    op.drop_index(op.f('ix_customers_region'), table_name='customers')
    op.drop_index(op.f('ix_customers_customer_type'), table_name='customers')
    op.drop_index(op.f('ix_customers_customer_ref'), table_name='customers')
    op.drop_index(op.f('ix_customers_tenant_id'), table_name='customers')
    op.drop_table('customers')
