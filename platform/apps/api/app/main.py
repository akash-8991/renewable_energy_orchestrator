from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import auth, health

app = FastAPI(
    title="Renewable Energy Orchestrator — Platform API",
    version="0.1.0",
    description=(
        "Ingestion, digital twin, decision ledger, approvals, connectors, "
        "export and tenant administration. Optimization, agent orchestration "
        "and OT command execution run as separate isolated services and "
        "communicate over the Redis event bus / internal APIs — see "
        "platform/docs/ARCHITECTURE.md."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dashboard is same-origin in prod deployments; loosened for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
