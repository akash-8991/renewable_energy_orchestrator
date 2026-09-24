"""Optimizer worker entrypoint.

Placeholder for scaffolding phase — subscribes to the decision-cycle stream
and logs heartbeats so `docker compose up` proves the service boots and can
reach Postgres/Redis. The real rolling-horizon MILP solve (TRD §4) and
independent feasibility validator land in the "Intelligence" build phase.
"""

import logging
import time

from reo_common.events import EventBus, STREAM_DECISION_CYCLE

logging.basicConfig(level=logging.INFO, format="%(asctime)s optimizer-worker %(message)s")
log = logging.getLogger("optimizer-worker")


def main() -> None:
    bus = EventBus()
    bus.ensure_group(STREAM_DECISION_CYCLE, "optimizer-worker")
    log.info("optimizer-worker started, watching %s", STREAM_DECISION_CYCLE)
    while True:
        events = bus.consume(STREAM_DECISION_CYCLE, "optimizer-worker", "worker-1", block_ms=5000)
        for entry_id, event in events:
            log.info("received decision-cycle event %s (solver not yet implemented)", event.correlation_id)
            bus.ack(STREAM_DECISION_CYCLE, "optimizer-worker", entry_id)
        if not events:
            time.sleep(1)


if __name__ == "__main__":
    main()
