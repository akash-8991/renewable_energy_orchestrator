"""agent observability (call logs, eval runs) + connector kind

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agent_call_logs',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('agent', sa.String(length=60), nullable=False),
    sa.Column('correlation_id', sa.String(length=80), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('schema_valid', sa.Boolean(), nullable=False),
    sa.Column('retried', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_call_logs_agent'), 'agent_call_logs', ['agent'], unique=False)
    op.create_index(op.f('ix_agent_call_logs_correlation_id'), 'agent_call_logs', ['correlation_id'], unique=False)
    op.create_index('ix_agent_call_logs_tenant_agent_created', 'agent_call_logs', ['tenant_id', 'agent', 'created_at'], unique=False)
    op.create_index(op.f('ix_agent_call_logs_tenant_id'), 'agent_call_logs', ['tenant_id'], unique=False)

    op.create_table('agent_eval_runs',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('triggered_by', sa.String(length=200), nullable=True),
    sa.Column('model_provider', sa.String(length=30), nullable=False),
    sa.Column('total_cases', sa.Integer(), nullable=False),
    sa.Column('passed_cases', sa.Integer(), nullable=False),
    sa.Column('results', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_eval_runs_tenant_created', 'agent_eval_runs', ['tenant_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_agent_eval_runs_tenant_id'), 'agent_eval_runs', ['tenant_id'], unique=False)

    op.add_column('connectors', sa.Column('kind', sa.String(length=30), server_default='generic', nullable=False))


def downgrade() -> None:
    op.drop_column('connectors', 'kind')

    op.drop_index(op.f('ix_agent_eval_runs_tenant_id'), table_name='agent_eval_runs')
    op.drop_index('ix_agent_eval_runs_tenant_created', table_name='agent_eval_runs')
    op.drop_table('agent_eval_runs')

    op.drop_index(op.f('ix_agent_call_logs_tenant_id'), table_name='agent_call_logs')
    op.drop_index('ix_agent_call_logs_tenant_agent_created', table_name='agent_call_logs')
    op.drop_index(op.f('ix_agent_call_logs_correlation_id'), table_name='agent_call_logs')
    op.drop_index(op.f('ix_agent_call_logs_agent'), table_name='agent_call_logs')
    op.drop_table('agent_call_logs')
