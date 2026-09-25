"""Cross-cutting shared kernel for the Renewable Energy Orchestrator platform:
config, the vendor-neutral LLM model gateway, the Redis event bus, JWT/RBAC
auth, the secrets vault, and digital-twin freshness helpers.

The canonical data model, DB connection layer, safety/guardrail checks, the
policy/dispatch engine and the evaluation harness each live in their own
top-level package (platform/models, database, guardrails, policy,
evaluation) rather than here — see platform/docs/ARCHITECTURE.md for the
full repository layout and why it's split this way. Every service depends
on this package (`pip install -e`) instead of duplicating auth/tenancy/
model-gateway logic.
"""
