"""Export worker entrypoint (placeholder for scaffolding phase).

Streaming XLSX generation (Decisions/Signals/Reasoning/Approvals/
Acknowledgements/Metadata sheets) lands in the "Platform governance" build
phase. This proves Redis connectivity for the export-job queue.
"""

import logging
import time

from reo_common.events import EventBus

logging.basicConfig(level=logging.INFO, format="%(asctime)s export-worker %(message)s")
log = logging.getLogger("export-worker")

STREAM_EXPORT_REQUESTED = "reo.export.requested"


def main() -> None:
    bus = EventBus()
    bus.ensure_group(STREAM_EXPORT_REQUESTED, "export-worker")
    log.info("export-worker started, watching %s", STREAM_EXPORT_REQUESTED)
    while True:
        events = bus.consume(STREAM_EXPORT_REQUESTED, "export-worker", "worker-1", block_ms=5000)
        for entry_id, event in events:
            log.info("received export request %s (writer not yet implemented)", event.correlation_id)
            bus.ack(STREAM_EXPORT_REQUESTED, "export-worker", entry_id)
        if not events:
            time.sleep(1)


if __name__ == "__main__":
    main()
