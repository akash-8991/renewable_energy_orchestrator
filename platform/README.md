# Renewable Energy Orchestrator (REO) — Platform

Implementation of the 8-document REO spec (`../renewable_energy_orchestrator/`) — a decision and
control platform coordinating solar, wind, battery storage, demand flexibility, grid
interconnection and market participation on a 10-minute cycle, with an LLM agent layer that
prepares evidence and explanations while a deterministic optimizer and a segregated OT command
gateway retain sole authority over anything that touches equipment.

See `docs/ARCHITECTURE.md` for the build-to-spec traceability map and `docs/SIMPLIFICATIONS.md`
for every deliberate scope decision made to build this without a real client engagement.

## Quickstart (Docker Compose)

Requires Docker Desktop.

```bash
cd platform/infra
docker compose up -d --build
```

This brings up: Postgres+TimescaleDB, Redis, MinIO, runs migrations, then starts `api`,
`optimizer-worker`, `agent-worker`, `ot-gateway-sim`, `edge-simulator`, `export-worker`, `web`.

Seed the reference demo tenant (5 solar farms, 3 wind farms, 2 BESS, 6 industrial consumers, one
grid interconnection, and one user per role):

```bash
docker compose run --rm api python /app/platform/db/seed.py
```

Then:
- API: http://localhost:8000 (docs at `/docs`)
- Dashboard: http://localhost:5173
- OT gateway simulator: http://localhost:8010/health
- MinIO console: http://localhost:9001 (`reo-minio` / `reo-minio-secret`)

Demo login: tenant slug `demo-utility`, any seeded email (e.g. `tenant.admin@demo-utility.test`),
password `Password123!` (local/demo only — see `db/seed.py`).

The dashboard has 10 workspaces (sidebar): Portfolio Operations, Decision Centre, Approval Inbox,
Live Signal Monitor, Connector Studio, Policy Studio, Simulation Lab, Audit & Exports, Tenant
Administration, Platform Operations — each role sees a different subset per its RBAC permissions
(e.g. only `portfolio_manager` can drive Simulation Lab; only `tenant_admin`/`platform_admin` can
provision users/tenants).

## Run the demo script

`platform/tests/demo_runner.py` drives the live stack through all 10 steps of doc 08 §4's
demonstration script (baseline → weather/price/outage/grid/demand shocks → mode progression →
bounded-autonomy policy → an injected-instruction guardrail check → a governed export) and asserts
the expected outcome at each step — useful both as a live demo narration script and as an
end-to-end smoke test:

```bash
cd platform
python3 tests/demo_runner.py --base-url http://localhost:8000
```

### Ports

Postgres and Redis are mapped to non-default host ports (`5433`, `6380`) because a native/
Homebrew Postgres or Redis is common on developer machines and would otherwise silently steal the
connection on `localhost`. Containers still talk to each other as `postgres:5432` / `redis:6379`
internally — only host-side tooling (a local `alembic`, `psql`, `redis-cli`) needs the mapped ports.

## Model provider

Defaults to a deterministic mock model gateway (zero cost, zero API key, schema-valid canned
responses) so the whole pipeline runs out of the box. For real agent reasoning, set in
`platform/.env` (copy from `.env.example`) or as compose environment variables:

```bash
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

## Running tests

```bash
cd platform
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/reo_common
pip install -r apps/api/requirements.txt -r apps/optimizer-worker/requirements.txt -r apps/agent-worker/requirements.txt
pip install pytest
DATABASE_URL=postgresql+psycopg2://reo:reo@127.0.0.1:5433/reo pytest tests -v
```

## AWS deployment (Terraform, not applied)

`platform/infra/terraform` provisions a simplified single-account AWS reference deployment (VPC
with an isolated OT-DMZ tier, ECS Fargate, RDS PostgreSQL, ElastiCache Redis, S3, Secrets
Manager/KMS, ALB). It validates cleanly (`terraform validate`) but has **not** been applied — no
AWS resources exist from this build. See `docs/SIMPLIFICATIONS.md` for what differs from the
spec's full reference architecture and why.

```bash
cd platform/infra/terraform
terraform init
terraform plan   # requires AWS credentials; review before ever running apply
```
