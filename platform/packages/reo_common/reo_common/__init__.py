"""Shared kernel for the Renewable Energy Orchestrator platform.

Every service (api, optimizer-worker, agent-worker, ot-gateway-sim,
edge-simulator, export-worker) depends on this package instead of
duplicating the canonical data model, tenancy enforcement, auth,
model-gateway or secrets-provider logic.
"""
