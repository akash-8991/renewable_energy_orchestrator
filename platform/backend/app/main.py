import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from reo_common.config import get_settings, production_config_problems

from .security_http import SecurityHeadersMiddleware

from .ingestion import connector_poller, folder_watcher, telemetry_consumer
from .routers import (
    actions,
    admin,
    audit,
    auth,
    configuration,
    connectors,
    customers,
    decisions,
    exports,
    forecasting,
    governance,
    health,
    ingestion,
    observability,
    operations,
    portfolio_registry,
    signals,
    simulation,
    twin,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s api %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    problems = production_config_problems(get_settings())
    if problems and not get_settings().allow_insecure_defaults:
        raise RuntimeError(
            "refusing to start with insecure configuration for ENVIRONMENT="
            f"{get_settings().environment!r}:\n  - " + "\n  - ".join(problems)
            + "\n(see docs/DEPLOYMENT.md, 'Production hardening checklist')"
        )
    for problem in problems:
        logging.getLogger("api.security").warning("INSECURE CONFIG ALLOWED (ALLOW_INSECURE_DEFAULTS): %s", problem)
    telemetry_consumer.start_background_thread()
    folder_watcher.start_background_thread()
    connector_poller.start_background_thread()
    yield
    telemetry_consumer.stop()
    folder_watcher.stop()
    connector_poller.stop()


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
    docs_url="/docs" if get_settings().enable_api_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if get_settings().enable_api_docs else None,
)

_origins = [o.strip() for o in get_settings().cors_allowed_origins.split(",") if o.strip()] or ["*"]
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,  # "*" only for local development — production_config_problems() refuses it elsewhere
    allow_credentials=_origins != ["*"],
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
app.include_router(simulation.router)
app.include_router(signals.router)
app.include_router(actions.router)
app.include_router(observability.router)
app.include_router(customers.router)
app.include_router(operations.router)
app.include_router(configuration.router)
app.include_router(forecasting.router)
app.include_router(portfolio_registry.router)
