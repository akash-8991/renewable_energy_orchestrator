"""Optimizer worker entrypoint: runs a decision cycle on a fixed cadence and
on-demand when a `reo.decision.cycle` event arrives (e.g. a future
Scenario Lab "recompute now" action, or an event-driven replan trigger).

PRD non-functional target is a 10-minute cadence; this defaults to a
shorter interval (120s) so the demo/dev loop doesn't require a 10-minute
wait to see a new Decision appear — override via DECISION_CYCLE_SECONDS.
"""

import logging
import os
import time

from cycle import run_cycle
from reo_common.events import EventBus, STREAM_DECISION_CYCLE

logging.basicConfig(level=logging.INFO, format="%(asctime)s optimizer-worker %(message)s")
log = logging.getLogger("optimizer-worker")

CYCLE_SECONDS = int(os.environ.get("DECISION_CYCLE_SECONDS", "120"))


def main() -> None:
    bus = EventBus()
    bus.ensure_group(STREAM_DECISION_CYCLE, "optimizer-worker")
    log.info("optimizer-worker started, cadence=%ss, watching %s for on-demand triggers", CYCLE_SECONDS, STREAM_DECISION_CYCLE)

    last_run = 0.0
    while True:
        events = bus.consume(STREAM_DECISION_CYCLE, "optimizer-worker", "worker-1", block_ms=2000)
        for entry_id, event in events:
            log.info("on-demand decision cycle triggered by %s", event.correlation_id)
            try:
                run_cycle(trigger=event.data.get("trigger", "on_demand"))
            except Exception:
                log.exception("on-demand cycle failed")
            bus.ack(STREAM_DECISION_CYCLE, "optimizer-worker", entry_id)
            last_run = time.time()

        if time.time() - last_run >= CYCLE_SECONDS:
            try:
                run_cycle(trigger="scheduled")
            except Exception:
                log.exception("scheduled cycle failed")
            last_run = time.time()


if __name__ == "__main__":
    main()
