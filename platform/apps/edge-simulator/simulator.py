"""Edge/portfolio simulator entrypoint (placeholder for scaffolding phase).

Real synthetic telemetry/weather/market generation for the seeded reference
portfolio (5 solar, 3 wind, 2 BESS, 6 industrial consumers) lands in the
"Data layer" build phase. For now this proves DB + Redis connectivity.
"""

import logging
import time

from reo_common.db import SessionLocal, break_glass_cross_tenant
from reo_common.events import EventBus
from reo_common.models import Tenant
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s edge-simulator %(message)s")
log = logging.getLogger("edge-simulator")


def main() -> None:
    EventBus()  # proves redis connectivity at boot
    while True:
        db = SessionLocal()
        try:
            with break_glass_cross_tenant():
                count = len(db.execute(select(Tenant)).scalars().all())
            log.info("heartbeat: %d tenant(s) visible, telemetry generation not yet implemented", count)
        finally:
            db.close()
        time.sleep(10)


if __name__ == "__main__":
    main()
