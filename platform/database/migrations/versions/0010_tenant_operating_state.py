"""tenant operating state (idle|running) — gates the optimizer decision cycle

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('operating_state', sa.String(length=20), nullable=False, server_default='idle'))
    op.add_column('tenants', sa.Column('operating_state_changed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('tenants', 'operating_state_changed_at')
    op.drop_column('tenants', 'operating_state')
