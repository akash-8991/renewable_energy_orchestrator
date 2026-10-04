"""Optimizer worker entrypoint: runs a decision cycle on a fixed cadence and
on-demand when a `reo.decision.cycle` event arrives — published by the api
when a data source delivers changed data (connector poller / folder watcher,
trigger "event:data_change"; see reo_common.events.request_decision_cycle).

PRD non-functional target is a 10-minute cadence; this defaults to a
shorter interval (120s) so the demo/dev loop doesn't require a 10-minute
wait to see a new Decision appear — override via DECISION_CYCLE_SECONDS.
"""

import logging
import os
import time

from cycle import run_cycle, running_tenant_ids
from housekeeping import prune_observability
from reo_common.config import get_settings
from reo_common.events import EventBus, STREAM_DECISION_CYCLE
from reo_common.heartbeat import beat

logging.basicConfig(level=logging.INFO, format="%(asctime)s optimizer-worker %(message)s")
log = logging.getLogger("optimizer-worker")

CYCLE_SECONDS = int(os.environ.get("DECISION_CYCLE_SECONDS", "120"))
# `run_cycle` returns None for several "not ready yet" conditions (no tenant
# seeded, no portfolio, no grid asset, forecasts didn't load) as well as
# genuine errors it swallowed — none of those should cost a full
# CYCLE_SECONDS wait before the next attempt. Only a *successful* cycle
# earns the full cadence delay; anything else retries soon.
RETRY_SECONDS = 10


def main() -> None:
    bus = EventBus()
    bus.ensure_group(STREAM_DECISION_CYCLE, "optimizer-worker")
    log.info("optimizer-worker started, cadence=%ss, watching %s for on-demand triggers", CYCLE_SECONDS, STREAM_DECISION_CYCLE)

    next_due = 0.0  # run immediately on startup
    next_housekeeping = 0.0
    while True:
        beat("optimizer-worker")
        events = bus.consume(STREAM_DECISION_CYCLE, "optimizer-worker", "worker-1", block_ms=2000)
        for entry_id, event in events:
            log.info("on-demand decision cycle triggered by %s", event.correlation_id)
            try:
                if run_cycle(trigger=event.data.get("trigger", "on_demand"), tenant_id=event.tenant_id):
                    # this cycle already planned on the freshest data, so
                    # the scheduled cadence restarts from now
                    next_due = time.time() + CYCLE_SECONDS
            except Exception:
                log.exception("on-demand cycle failed")
            bus.ack(STREAM_DECISION_CYCLE, "optimizer-worker", entry_id)

        if time.time() >= next_housekeeping:
            prune_observability(get_settings().observability_retention_days)
            next_housekeeping = time.time() + 6 * 3600

        if time.time() >= next_due:
            decision_id = None
            for tenant_id in running_tenant_ids():  # every started tenant is planned for, not just the first
                try:
                    decision_id = run_cycle(trigger="scheduled", tenant_id=tenant_id) or decision_id
                except Exception:
                    log.exception("scheduled cycle failed for tenant %s", tenant_id)
            next_due = time.time() + (CYCLE_SECONDS if decision_id else RETRY_SECONDS)


if __name__ == "__main__":
    main()
