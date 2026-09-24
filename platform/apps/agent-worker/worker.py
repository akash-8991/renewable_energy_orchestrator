"""Agent worker entrypoint (placeholder for scaffolding phase).

The 10-agent roster + orchestrator (doc 07) lands in the "Agent layer"
build phase, in `agents/`. For now this proves the model gateway wiring —
it resolves and logs which provider is configured (Anthropic/OpenAI/mock).
"""

import logging
import time

from reo_common.events import EventBus, STREAM_DECISION_CYCLE
from reo_common.model_gateway import get_model_gateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s agent-worker %(message)s")
log = logging.getLogger("agent-worker")


def main() -> None:
    gateway = get_model_gateway()
    log.info("agent-worker started, model gateway provider=%s model=%s", gateway.provider_name, getattr(gateway, "model_name", "n/a"))
    bus = EventBus()
    bus.ensure_group(STREAM_DECISION_CYCLE, "agent-worker")
    while True:
        events = bus.consume(STREAM_DECISION_CYCLE, "agent-worker", "worker-1", block_ms=5000)
        for entry_id, event in events:
            log.info("received decision-cycle event %s (agent roster not yet implemented)", event.correlation_id)
            bus.ack(STREAM_DECISION_CYCLE, "agent-worker", entry_id)
        if not events:
            time.sleep(1)


if __name__ == "__main__":
    main()
