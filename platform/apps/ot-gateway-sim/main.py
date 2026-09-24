"""OT command gateway simulator (placeholder for scaffolding phase).

The real prepare/commit/acknowledge flow, independent deterministic
re-validation immediately pre-dispatch, and fault/latency injection for the
demo scenarios (doc 08 §4 steps 4-5 and 9) land in the "Safety & execution"
build phase. This is deliberately a *separate* service/container from the
api so the OT/command trust boundary in doc 05 §10 is real, not notional —
the api can only reach it over its HTTP API, never touch its DB directly.
"""

from fastapi import FastAPI

app = FastAPI(title="REO OT Gateway Simulator", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "note": "command validation endpoints land in the safety/execution build phase"}
