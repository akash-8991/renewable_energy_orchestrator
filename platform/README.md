# Renewable Energy Orchestrator (REO) — Platform

Implementation of the 8-document REO spec (`../renewable_energy_orchestrator/`) — a decision and
control platform coordinating solar, wind, battery storage, demand flexibility, grid
interconnection and market participation on a 10-minute cycle, with an LLM agent layer that
prepares evidence and explanations while a deterministic optimizer and a segregated OT command
gateway retain sole authority over anything that touches equipment.

See `docs/ARCHITECTURE.md` for the build-to-spec traceability map, `docs/SIMPLIFICATIONS.md` for
every deliberate scope decision made to build this without a real client engagement, and
`docs/PRODUCTION_READINESS_REVIEW.md` for a direct answer on deployment readiness. For full
click-by-click steps to run this locally or actually stand it up on AWS (or another cloud), see
**`docs/DEPLOYMENT.md`** — the quickstart below is the condensed version.

## Repository layout

Each top-level directory is one concern, so a reader can go straight to the part of the system
they care about instead of hunting through a single `apps/`:

```
platform/
├── backend/          FastAPI HTTP API — ingestion, digital twin, decisions, governance,
│                      connectors, exports, audit, admin, observability. The dashboard's
│                      only entry point into the platform.
├── frontend/          React + TypeScript + Vite dashboard (14 workspaces).
├── agent/              The 9 specialist LLM agents (doc 07) + their orchestration loop
│                      (worker.py). Agents only ever produce typed JSON evidence — never a
│                      command — consumed by policy/ below.
├── policy/            The actual decision-and-dispatch authority: the deterministic MILP
│                      solver, the 24h scenario-comparison engine, the action builder that
│                      turns a solved plan into governed Actions, and the autonomy/execution
│                      engine (policy/engine/) that decides Observe/Recommend/Approval/
│                      Autonomous disposition and forwards approved commands to guardrails/.
├── guardrails/         Independent safety checks, structurally separate from the things they
│                      check: the feasibility validator (re-checks the solver's own plan from
│                      first principles), SSRF egress hardening, the model-call rate limiter,
│                      PII redaction, and ot-gateway-sim/ (independent re-validation
│                      immediately pre-dispatch + simulated SCADA acknowledgement).
├── evaluation/         Agent observability persistence + the fixed-scenario evaluation
│                      harness — the concrete thing behind the model_admin role.
├── models/             The canonical SQLAlchemy data model (Tenant, Asset, Decision, Action,
│                      DocumentIntake, AgentCallLog, ...) — one schema, shared by every service.
├── database/           DB connection/tenant-isolation layer, Alembic migrations, seed script.
├── output/             Reporting/export: the hash-chained audit log and the Excel
│                      evidence-pack export worker.
├── infrastructure/     docker-compose.yml, Dockerfiles' build context, Terraform (AWS
│                      reference deployment, written but not applied), and edge-simulator/
│                      (synthetic telemetry standing in for real smart-meter/SCADA feeds).
├── packages/
│   └── reo_common/     Cross-cutting shared kernel every service pip-installs: config, the
│                      vendor-neutral LLM model gateway, the Redis event bus, JWT/RBAC auth,
│                      the secrets vault, digital-twin freshness helpers.
├── docs/                Architecture traceability, deliberate simplifications, production
│                      readiness review, demo script.
├── launcher/            reo_launcher.py — the stdlib-only Tk app packaged into REO-Launcher.exe
│                      (see docs/DEPLOYMENT.md Part 0) that gets a Windows machine with only
│                      Docker Desktop from "double-click" to a running dashboard.
└── tests/               pytest suite + demo_runner.py (drives the live stack through the
                        doc 08 §4 demo script end-to-end).
```

Why split this way instead of one folder per deployable service: several of the concerns above
(`models`, `guardrails`, `policy/engine`, `evaluation`) are imported by *multiple* services (e.g.
`backend` and `agent` both read `models.canonical`; `policy`'s cycle imports
`guardrails.validator`) — grouping by concern rather than by container makes it obvious which
piece of logic to change for a given change, independent of which service happens to run it.
Each Python service's own `Dockerfile` copies exactly the shared top-level packages it needs and
sets `PYTHONPATH=/app/platform:/app/platform/<service>` so both styles resolve: dotted imports
like `from models.canonical import Asset` for the shared packages, and flat imports like
`from solver import ...` for files within that service's own directory.

## Quickstart

**On Windows and don't want to touch a terminal?** Download `REO-Launcher.exe` from the
[Releases page](https://github.com/akash-8991/renewable_energy_orchestrator/releases), double-click
it, and follow the prompts — it needs only Docker Desktop, downloads the platform source itself,
asks for an LLM API key (or skip for the free mock provider), and opens the dashboard when it's
ready. See `docs/DEPLOYMENT.md` Part 0 for details, or the launcher's source at
`platform/launcher/reo_launcher.py`.

Otherwise, from a terminal (any OS):

### Docker Compose

Requires Docker Desktop.

```bash
cd platform/infrastructure
docker compose up -d --build
```

This brings up: Postgres+TimescaleDB, Redis, SeaweedFS (S3-compatible object storage — MinIO until
MinIO discontinued free Docker image distribution entirely, see `docs/SIMPLIFICATIONS.md`),
`source-db` (a plain Postgres pre-loaded with
the reference dataset as real SQL tables — stands in for "a client's own database" for Connector
Studio's `database` kind, see `docs/DEPLOYMENT.md` A9a), runs migrations, then starts `api`,
`optimizer-worker`, `agent-worker`, `ot-gateway-sim`, `edge-simulator`, `export-worker`, `web`.

Seed the reference demo tenant (5 solar farms, 3 wind farms, 2 BESS, 6 industrial consumers, one
grid interconnection, and one user per role):

```bash
docker compose run --rm api python /app/platform/database/seed.py
```

Then:
- API: http://localhost:8000 (docs at `/docs`)
- Dashboard: http://localhost:5173
- OT gateway simulator: http://localhost:8010/health
- SeaweedFS file browser (object storage): http://localhost:9001

Demo login: tenant slug `demo-utility`, any seeded email (e.g. `tenant.admin@demo-utility.test`),
password `Password123!` (local/demo only — see `database/seed.py`). For the full table of all 9
demo accounts and what each role can do, see **`docs/DEPLOYMENT.md`** (§ A7, "Demo accounts").

A fresh tenant lands on Portfolio Operations **idle** — Start Optimizer only unlocks once an
active `database` or `data_table` connector exists in Connector Studio (Document Intake uploads
still ingest real data but no longer unlock it on their own). See `docs/DEPLOYMENT.md` A7 step 3
and `docs/SIMPLIFICATIONS.md`'s "Portfolio-wide start/stop gate" section.

The dashboard has 14 workspaces (sidebar): Portfolio Operations, Decision Centre, Customers,
Approval Inbox, Live Signal Monitor, Action Tickets, Connector Studio, Document Intake,
Policy Studio, Simulation Lab, Agent Observability, Audit & Exports, Tenant Administration,
Platform Operations — each role
sees a different subset per its RBAC permissions (e.g. only `portfolio_manager` can drive
Simulation Lab; only `tenant_admin` can provision users/tenants; only `model_admin` can trigger an
evaluation run) — except `platform_admin`, which is a deliberate superuser and can do all of it
(see `docs/DEPLOYMENT.md`'s demo-account table).

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

Defaults to **OpenRouter** (https://openrouter.ai — one OpenAI-compatible endpoint in front of
many providers/models), with automatic fallback to a deterministic mock gateway (zero cost, zero
API key, schema-valid canned responses) whenever no key is configured, so the whole pipeline still
runs out of the box with nothing set up.

For real agent reasoning, run `./infrastructure/set_api_key.sh OPENROUTER_API_KEY` (creates
`platform/infrastructure/.env` from `.env.example` if needed, prompts for the key with hidden
input, offers to set `MODEL_PROVIDER` to match) — or copy `platform/.env.example` to
`platform/infrastructure/.env` (used by `docker compose`, which loads `.env` from the compose
file's own directory) by hand and set:

```bash
MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...        # https://openrouter.ai/settings/keys
OPENROUTER_MODEL=openai/gpt-4o-mini    # any OpenRouter model slug
```

`platform/infrastructure/.env` is gitignored — never commit a real key. Anthropic and OpenAI
direct (non-OpenRouter) are also supported: set `MODEL_PROVIDER=anthropic` or `openai` and the
matching `*_API_KEY`/`*_MODEL` instead.

**Rate limiting:** every model-gateway call (every agent, the document-intake vision path) is
gated by a per-tenant Redis-backed cap enforced *before* the provider is called, so a blocked call
spends zero tokens — not a post-hoc throttle. Tune with `MODEL_RATE_LIMIT_PER_MINUTE` /
`MODEL_RATE_LIMIT_PER_DAY` (defaults: 20/min, 2000/day). Ignored for the mock gateway.

## Running tests

`packages/reo_common` requires **Python 3.11+** (it's a pyproject.toml-only package — no
`setup.py` — so it also needs a reasonably modern pip). On macOS the `python3` on `PATH` is often
Apple's bundled 3.9.x, which fails partway through install with a confusing error, so name the
interpreter explicitly (`python3.11`/`python3.12`/`python3.13`, `brew install python@3.12` if you
don't have one). Also run every command below from `platform/` — `pip install -e packages/reo_common`
resolves that path relative to your current directory, and fails with *"is not a valid editable
requirement"* if you're anywhere else (e.g. still in `infrastructure/`).

```bash
cd platform
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e packages/reo_common
pip install -r backend/requirements.txt -r policy/requirements.txt -r agent/requirements.txt
pip install pytest
DATABASE_URL=postgresql+psycopg2://reo:reo@127.0.0.1:5433/reo pytest tests -v
```

## AWS deployment (Terraform, not applied)

`platform/infrastructure/terraform` provisions a simplified single-account AWS reference
deployment (VPC with an isolated OT-DMZ tier, ECS Fargate, RDS PostgreSQL, ElastiCache Redis, S3,
Secrets Manager/KMS, ALB). It validates cleanly (`terraform validate`) but has **not** been
applied — no AWS resources exist from this build. See `docs/SIMPLIFICATIONS.md` for what differs
from the spec's full reference architecture and why.

```bash
cd platform/infrastructure/terraform
terraform init
terraform plan   # requires AWS credentials; review before ever running apply
```
