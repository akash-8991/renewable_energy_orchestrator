import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .ingestion import folder_watcher, telemetry_consumer
from .routers import admin, audit, auth, connectors, decisions, exports, governance, health, ingestion, twin

logging.basicConfig(level=logging.INFO, format="%(asctime)s api %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    telemetry_consumer.start_background_thread()
    folder_watcher.start_background_thread()
    yield
    telemetry_consumer.stop()
    folder_watcher.stop()


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
    lifespan=lifespan,
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
app.include_router(ingestion.router)
app.include_router(twin.router)
app.include_router(decisions.router)
app.include_router(governance.router)
app.include_router(connectors.router)
app.include_router(exports.router)
app.include_router(audit.router)
app.include_router(admin.router)
