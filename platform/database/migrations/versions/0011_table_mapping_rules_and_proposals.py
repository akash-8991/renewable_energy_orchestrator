"""generic table mapping rules + pending mapping proposals

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'table_mapping_rules',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('column_signature', sa.String(length=64), nullable=False),
        sa.Column('file_kind', sa.String(length=20), nullable=False),
        sa.Column('column_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sample_filename', sa.String(length=300), nullable=False),
        sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('times_reused', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'column_signature', name='uq_table_mapping_rule'),
    )
    op.create_index(op.f('ix_table_mapping_rules_tenant_id'), 'table_mapping_rules', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_table_mapping_rules_column_signature'), 'table_mapping_rules', ['column_signature'], unique=False)

    op.create_table(
        'data_mapping_proposals',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('filename', sa.String(length=300), nullable=False),
        sa.Column('column_signature', sa.String(length=64), nullable=False),
        sa.Column('file_kind', sa.String(length=20), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('reasoning', sa.Text(), nullable=False),
        sa.Column('column_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sample_preview', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('quarantine_key', sa.String(length=300), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_data_mapping_proposals_tenant_id'), 'data_mapping_proposals', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_data_mapping_proposals_column_signature'), 'data_mapping_proposals', ['column_signature'], unique=False)
    op.create_index('ix_data_mapping_proposals_tenant_created', 'data_mapping_proposals', ['tenant_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_data_mapping_proposals_tenant_created', table_name='data_mapping_proposals')
    op.drop_index(op.f('ix_data_mapping_proposals_column_signature'), table_name='data_mapping_proposals')
    op.drop_index(op.f('ix_data_mapping_proposals_tenant_id'), table_name='data_mapping_proposals')
    op.drop_table('data_mapping_proposals')

    op.drop_index(op.f('ix_table_mapping_rules_column_signature'), table_name='table_mapping_rules')
    op.drop_index(op.f('ix_table_mapping_rules_tenant_id'), table_name='table_mapping_rules')
    op.drop_table('table_mapping_rules')
