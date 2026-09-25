"""convert telemetry and forecasts to timescale hypertables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24

"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # migrate_data=true because 0002 may have already inserted seed rows in dev.
    op.execute(
        "SELECT create_hypertable('telemetry', 'event_time', if_not_exists => TRUE, migrate_data => TRUE);"
    )
    op.execute(
        "SELECT create_hypertable('forecasts', 'valid_time', if_not_exists => TRUE, migrate_data => TRUE);"
    )
    # Retention/compression policy scaffold (values tuned during capacity testing per doc 06 §5).
    op.execute("SELECT add_retention_policy('telemetry', INTERVAL '3 years', if_not_exists => TRUE);")


def downgrade() -> None:
    pass
