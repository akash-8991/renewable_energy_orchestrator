"""retention policy on the forecasts hypertable

Every decision cycle writes ~1,000 forecast rows (24h horizon x every
forecastable asset x 3 quantiles), i.e. roughly 700k rows/day at the default
2-minute cadence. Only telemetry had a retention policy, so forecasts grew
without bound. 90 days keeps every forecast a recent decision may be asked
about; the Decision row itself (the ledger) is never pruned.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SELECT add_retention_policy('forecasts', INTERVAL '90 days', if_not_exists => TRUE);")


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('forecasts', if_exists => TRUE);")
