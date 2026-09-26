# Platform Usage Guide (Post-Deployment)

This is the operator's runbook for a **already-running** deployment — local/demo (Docker Compose)
or production (AWS, Part B of `docs/DEPLOYMENT.md`). If the platform isn't running yet, start with
`docs/DEPLOYMENT.md` instead; this guide picks up from there.

Every endpoint below is real and is exercised by this repo's own `tests/demo_runner.py` and/or
`tests/` suite — nothing here is aspirational. Where a capability is intentionally limited (most
notably SCADA/OT control), that's called out explicitly rather than glossed over — see
[§8](#8-battery-charging--renewable-optimization-scada-control-read-this-one-carefully).

## Contents

1. [First login](#1-first-login)
2. [Creating additional users and tenants](#2-creating-additional-users-and-tenants)
3. [Calling the API directly (curl / Postman / scripts)](#3-calling-the-api-directly-curl--postman--scripts)
4. [The Start Optimizer gate — what has to be connected first](#4-the-start-optimizer-gate--what-has-to-be-connected-first)
5. [Connecting a backend database](#5-connecting-a-backend-database)
6. [Connecting a file / data-table feed](#6-connecting-a-file--data-table-feed)
7. [Connecting a live weather feed](#7-connecting-a-live-weather-feed)
8. [Connecting an energy-procurement / market-data site](#8-connecting-an-energy-procurement--market-data-site)
9. [Connecting IoT telemetry sources](#9-connecting-iot-telemetry-sources)
10. [Battery charging & renewable-optimization SCADA control (read this one carefully)](#10-battery-charging--renewable-optimization-scada-control-read-this-one-carefully)
11. [LLM / model gateway setup](#11-llm--model-gateway-setup)
12. [Model gateway resilience (circuit breaker)](#12-model-gateway-resilience-circuit-breaker)
13. [Real SSO / identity provider setup](#13-real-sso--identity-provider-setup)
14. [RBAC roles reference](#14-rbac-roles-reference)
15. [Autonomy mode & governance](#15-autonomy-mode--governance)
16. [Exporting audit evidence](#16-exporting-audit-evidence)
17. [Local demo vs. AWS production — what actually differs](#17-local-demo-vs-aws-production--what-actually-differs)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. First login

Open the dashboard (`http://localhost:5173` locally, or your CloudFront URL in production — see
`docs/DEPLOYMENT.md` B12) and sign in with the tenant slug + email + password created by the seed
script, or via SSO (§13).

Seeded demo accounts (local/demo only — never present in a real production tenant you provision
yourself): tenant slug `demo-utility`, password `Password123!` for all nine, any of:

| Email | Role |
|---|---|
| `viewer@demo-utility.test` | Viewer |
| `operator@demo-utility.test` | Operator |
| `senior.operator@demo-utility.test` | Senior Operator |
| `portfolio.manager@demo-utility.test` | Portfolio Manager |
| `ot.admin@demo-utility.test` | OT Admin |
| `model.admin@demo-utility.test` | Model Admin |
| `tenant.admin@demo-utility.test` | Tenant Admin |
| `auditor@demo-utility.test` | Auditor / DPO |
| `platform.admin@demo-utility.test` | Platform Admin (superuser — every permission) |

For a real production tenant, don't use these — provision your own tenant and users (§2) and never
re-seed `database/seed.py` against a real environment (it's idempotent and safe to run, but it only
ever creates the fixed demo portfolio/users, which you don't want in production).

## 2. Creating additional users and tenants

Both are `POST` endpoints under `/admin`, gated by RBAC (§14). There's no dashboard form for tenant
creation (deliberately platform-admin-only, break-glass-audited); user creation has one in **Tenant
Administration**, or call the API directly.

**Create a new tenant** (requires `manage:tenants` — Platform Admin):

```bash
curl -X POST http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "slug": "acme-utility",
    "name": "Acme Utility Co",
    "deployment_mode": "pooled",
    "data_residency": "EU",
    "timezone": "Europe/London",
    "market_area": "GB"
  }'
```

**Create a new user in your own tenant** (requires `manage:users` — Tenant Admin or Platform Admin):

```bash
curl -X POST http://localhost:8000/admin/users \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "email": "jane.ops@acme-utility.example",
    "display_name": "Jane Ops",
    "password": "a-real-password-not-this-one",
    "roles": ["operator"]
  }'
```

Valid `roles` values are the lowercase role names from §14 (a Tenant Admin cannot grant
`platform_admin` — that's provisioned separately, by another Platform Admin). A user's password is
never returned or logged; only its bcrypt hash is stored.

## 3. Calling the API directly (curl / Postman / scripts)

Every dashboard action is a plain REST call — useful for scripting, CI, or a system integration
that shouldn't go through the browser. Get a token the same way the login form does:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"tenant_slug":"demo-utility","email":"tenant.admin@demo-utility.test","password":"Password123!"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

The token is a JWT (`JWT_EXPIRY_MINUTES`, default 8 hours) — pass it as `Authorization: Bearer
$TOKEN` on every subsequent call. `GET /auth/me` returns your resolved roles/permissions if you need
to confirm what a token can do:

```bash
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer $TOKEN"
```

Full interactive API docs (every endpoint, request/response schema) are always at `/docs`
(`http://localhost:8000/docs` locally) — generated live from the FastAPI app, so they can never
drift from the real API surface.

## 4. The Start Optimizer gate — what has to be connected first

A fresh tenant sits **idle** on Portfolio Operations — no decisions, no live dashboard data — until
an operator connects a real data source and clicks **Start Optimizer**. The gate checks exactly one
thing: `GET /operations/status`'s `has_data_source`, which is `true` only once an active `database`
or `data_table` connector exists (§5/§6). Document Intake uploads, and `market_energy_purchase`/
`iot`/`generic`/`scada` connectors, all do real work but deliberately do **not** unlock this gate —
see `docs/SIMPLIFICATIONS.md`'s "Portfolio-wide start/stop gate" section for the full reasoning.

```bash
curl -s http://localhost:8000/operations/status -H "Authorization: Bearer $TOKEN"
# {"operating_state":"idle","has_data_source":false,"active_connector_count":0,"document_count":0}
```

Once `has_data_source` is `true`:

```bash
curl -X POST http://localhost:8000/operations/start -H "Authorization: Bearer $TOKEN"
```

## 5. Connecting a backend database

For pulling telemetry/customer data out of a client's own operational database (billing system,
metering database, SCADA historian export table — anything queryable over `postgresql://` today;
see §18 if yours is a different engine).

**Via the dashboard:** Connector Studio → **+ New connector** → kind **Database** → paste the
connection string → (optional) a table name → **Create (draft)** → have a *different* user click
**Test** then **Activate** (maker-checker: the creator can never also activate — this is enforced
server-side, not just hidden in the UI) → **Ingest now**.

**Via the API:**

```bash
curl -X POST http://localhost:8000/connectors \
  -H "Authorization: Bearer $CREATOR_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "name": "Client billing DB",
    "kind": "database",
    "endpoint_url": "postgresql://readonly_user:REDACTED@client-db.internal:5432/billing",
    "schema_mapping": {"table_name": "meter_readings"}
  }'
# -> {"id": "<connector_id>", "status": "draft", ...}

curl -X POST http://localhost:8000/connectors/<connector_id>/test  -H "Authorization: Bearer $CREATOR_TOKEN"
curl -X POST http://localhost:8000/connectors/<connector_id>/activate -H "Authorization: Bearer $ACTIVATOR_TOKEN"
curl -X POST http://localhost:8000/connectors/<connector_id>/ingest -H "Authorization: Bearer $ACTIVATOR_TOKEN"
```

**Production egress allow-list — read before you connect a real database.** `database`-kind
connectors are host-allow-listed (`DATABASE_CONNECTOR_ALLOWED_HOSTS` in
`backend/app/routers/connectors.py`), not generically SSRF-checked like an HTTP endpoint, because a
raw connection string's host would otherwise resolve inside your private network with no
opportunity for the usual loopback/link-local checks. Locally this list is just `["source-db"]` (the
bundled reference database). **Before pointing this at a real client database, add its real
hostname to that list and redeploy** — this is the one connector kind that needs a code change
(one line) rather than being purely dashboard-configurable, by design (a tenant admin should not be
able to make the platform connect to an arbitrary internal host by typing a connection string).

If the table name matches one of the reference dataset's own 8 table names
(`backend/app/ingestion/hackathon_dataset.py`'s `KNOWN_TABLE_KEYS` —
`renewable_generation`/`grid`/`market`/`external_weather`/`customer_demographics`/
`customer_energy_consumption_tariff`, plus `battery`/`scenario_actions` which are recognized but
report `not_mapped`), it's routed through canonical Asset/Customer mapping automatically. Any other
table name is expected in the generic shape: `asset_id, metric, event_time, value, unit`.

## 6. Connecting a file / data-table feed

For a CSV/JSON/XLSX export that gets dropped somewhere on a schedule (an SFTP pull job, a nightly
export, a shared drive), rather than a live database connection.

**Two address forms** for `endpoint_url`:
- An `http(s)://` URL — fetched fresh on every "Ingest now" (SSRF-checked: no loopback, link-local,
  or cloud-metadata addresses).
- A filename under the platform's watched local folder (`DATA_WATCH_DIR`, default `/data` — the
  same folder a background watcher already scans for new files, and the same mount point
  `docker-compose.yml`'s `../../data:/data` maps to your host's `data/` directory).

```bash
curl -X POST http://localhost:8000/connectors \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Nightly telemetry export", "kind": "data_table", "endpoint_url": "https://exports.example.com/telemetry-latest.csv"}'
```

Same test/activate/ingest sequence as §5. A row with an unrecognized column layout doesn't fail —
it goes through the generic-mapping agent (an LLM proposes a column mapping, a human approves it in
**Document Intake → Mapping proposals**, and the mapping is cached by exact column signature so an
identical file never needs a second model call — see `docs/SIMPLIFICATIONS.md`'s "generic
tabular-file mapping" section).

For structured telemetry files that don't need a connector at all, **Document Intake** accepts
direct upload (`.csv`/`.json`/`.xlsx`, or `.pdf`/`.png`/`.jpg`/`.docx` for unstructured documents
read via vision/text extraction) — it ingests real data the same way, it just doesn't unlock Start
Optimizer on its own (§4).

## 7. Connecting a live weather feed

No connector needed — this is a **Configuration Studio** toggle, and it's free and keyless
([Open-Meteo](https://open-meteo.com)).

**Via the dashboard:** Configuration Studio → *Live weather feed* → check **Enable live weather
feed** → enter your site's latitude/longitude → **Save configuration**.

**Via the API:**

```bash
curl -X PUT http://localhost:8000/configuration/settings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"live_weather_enabled": true, "weather_site_lat": 51.5074, "weather_site_lon": -0.1278}'
```

Takes effect on the *next* decision cycle (every ~10 minutes, or immediately if you trigger one
manually in Simulation Lab) — `policy/live_weather.py` fetches real forecasted cloud-cover/wind-speed
for the horizon and `policy/forecast.py` uses it in place of the synthetic solar diurnal curve /
seasonal-naive wind proxy for that cycle's solar/wind forecasts. If the API call fails for any
reason (network, rate limit, bad coordinates), it silently falls back to the synthetic model —
nothing here can fail a decision cycle.

If you need a **different or paid** weather provider (site-specific irradiance sensors, a
commercial forecast vendor), the real integration point is the same one described for market data
in §8: either point a `market_energy_purchase`/`iot`-style connector at it and adapt its response to
the shapes below, or fork `policy/live_weather.py`'s `fetch_live_weather()` to call that provider
instead of Open-Meteo — it returns a small `LiveWeatherForecast` dataclass that `forecast.py`
already knows how to use, so nothing downstream needs to change.

## 8. Connecting an energy-procurement / market-data site

For a power exchange, aggregator, or trading-desk API that publishes day-ahead/intraday prices.

**Contract:** the endpoint must return JSON — either a bare array, or `{"prices": [...]}` — of
objects shaped `{"timestamp": "<ISO 8601>", "price_per_mwh": <number>}`. If your provider's API
returns a different shape, put a small adapter/proxy in front of it that reshapes to this contract
(the parsing itself, `parse_market_price_entries()` in `backend/app/routers/connectors.py`, is a
pure function with its own unit tests — see `tests/test_connector_market_iot_ingest.py` — so you can
verify your adapter's output against the exact same logic before pointing a connector at it).

```bash
curl -X POST http://localhost:8000/connectors \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Day-ahead price feed", "kind": "market_energy_purchase", "endpoint_url": "https://market-provider.example.com/api/prices/day-ahead", "headers": {"Authorization": "Bearer <provider-api-key>"}}'
```

Test → activate → **Ingest now** the same way as §5/§6. A successful ingest writes a real `Forecast`
row (`variable="price"`, `model_version="live-market-v1"`) for every `grid_interconnection` asset in
your portfolio, for every timestamp the feed covers — the next decision cycle uses it in place of
the synthetic price curve for those timestamps. This does **not** unlock Start Optimizer (§4) — it's
real ingestion, just not one of the two kinds treated as "a connected data source."

There's no built-in scheduler yet — "Ingest now" is a manual/on-demand pull (call it from a cron job
or your own scheduler if you want it automatic; see `docs/PRODUCTION_READINESS_REVIEW.md` for this
noted as a still-open enhancement beyond a same-session scope).

## 9. Connecting IoT telemetry sources

For a smart-meter platform, an edge gateway aggregator, or any sensor/telemetry system exposing a
JSON API — same idea as §8, different shape.

**Contract:** the endpoint must return JSON in the same generic shape file uploads use — an array of
`{"asset_id": "...", "metric": "power_kw", "value": 123.4, "unit": "kW", "event_time": "<ISO 8601>"}`
objects (`asset_id` must match a real Asset's ID or name in your portfolio, exact case-insensitive
match — an unmatched reference is skipped and counted, never guessed).

```bash
curl -X POST http://localhost:8000/connectors \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Smart meter gateway", "kind": "iot", "endpoint_url": "https://iot-platform.example.com/api/readings/latest"}'
```

Same test/activate/ingest sequence; also doesn't unlock Start Optimizer (§4).

## 10. Battery charging & renewable-optimization SCADA control (read this one carefully)

**This is the one capability that is deliberately not "just configure a connector," and it never
will be — this is architecture, not an oversight or a to-do.**

Connector Studio's **SCADA** kind lets you register an endpoint and run a reachability test on it —
exactly like the other kinds. That is *all* it ever does. Grepping the codebase confirms nothing
outside `backend/app/routers/connectors.py` reads a `scada`-kind `Connector` row; there is no path
from it to an actual command. This is because the platform's non-negotiable architecture (doc 05
§10: "Direct browser-to-SCADA communication is prohibited") routes **every** real dispatch —
battery charge/discharge, curtailment, grid buy/sell, demand response — through one single,
independent, safety-critical path: `guardrails/ot-gateway-sim`, never a Connector Studio connection,
regardless of what's registered or activated there.

**Why this matters for you:** if you need this platform to actually move real equipment (a real BESS
charge/discharge setpoint, a real curtailment command to a real inverter), the integration point is
**not** Connector Studio. It's replacing what `ot-gateway-sim` does internally.

**How the real dispatch path works today (simulated, but the contract is real and stable):**

1. The policy engine decides an action needs autonomous or approved dispatch, and calls
   `policy/engine/execution.py`'s `dispatch_signal()`.
2. That function `POST`s to `{OT_GATEWAY_URL}/commands/prepare`, then (if accepted)
   `{OT_GATEWAY_URL}/commands/commit` — plain HTTP, JSON in, JSON out. `OT_GATEWAY_URL` is a normal
   environment variable (`reo_common/config.py`, default `http://ot-gateway-sim:8010`).
3. The request body (`CommandRequest` in `guardrails/ot-gateway-sim/main.py`) is:
   ```json
   {
     "signal_id": "...", "tenant_id": "...", "asset_id": "...",
     "command_type": "charge | discharge | buy | sell | curtail | demand_response",
     "setpoint_value": 150.0, "unit": "kW",
     "safety_limits": {"min": 0, "max": 200},
     "validity_start": "<ISO 8601>", "validity_end": "<ISO 8601>",
     "idempotency_key": "...", "nonce": "..."
   }
   ```
   and the response (`CommandResponse`) is `{"ready"|"acknowledged": bool, "reason": str|null,
   "checked_at": "<ISO 8601>"}`.
4. Today, `ot-gateway-sim` "executes" a commit by writing a simulated telemetry reading — there is no
   real hardware behind it, deliberately (doc 06 §8: "don't price/build real OT before a site
   survey").

**To connect real hardware**, a systems integrator would build a real service — a genuine OPC
UA/Modbus/vendor-API client that speaks to the real SCADA/BMS system — that implements this exact
`/commands/prepare` + `/commands/commit` contract (re-validating from a fresh read every time, per
doc 05 §7 — never trust the caller's own payload as truth), then point `OT_GATEWAY_URL` at it
instead of the simulator. Everything upstream (the optimizer, the policy engine, the approval
workflow, the dashboard) needs zero changes, because they only ever talk to "whatever's behind
`OT_GATEWAY_URL`," never to `ot-gateway-sim` by name.

**This is gated, on purpose, by more than a config change:**

- A tenant/asset can only leave `APPROVAL_REQUIRED` mode for `AUTONOMOUS_BOUNDED` (unattended
  dispatch) if a `safety_case_ref` is recorded first — the API rejects the mode change otherwise
  (§15). The platform enforces *recording* this reference; it cannot manufacture the actual hazard
  analysis and safety case behind it.
- Real OT integration is explicitly listed in `docs/PRODUCTION_READINESS_REVIEW.md` as the one item
  that "is not something to shortcut regardless of engineering effort available" — it needs a real
  site survey, a formal hazard analysis, commissioning, and a HIL test pass, none of which a
  software deployment guide can substitute for.

If what you actually need is *visibility* into a real SCADA system (reading its tag values as
telemetry, not commanding it), that's a much smaller, safer ask — point an `iot`-kind connector
(§9) at a read-only export/API from that system instead.

## 11. LLM / model gateway setup

The agent layer (9 specialist LLM agents that explain and prepare evidence — never make the actual
decision, see the non-negotiable architecture note in `docs/ARCHITECTURE.md`) needs a model
provider. Four are supported, selected by `MODEL_PROVIDER`:

| `MODEL_PROVIDER` | Also set | Notes |
|---|---|---|
| `mock` (default) | — | Zero-cost, deterministic, schema-valid but zero real reasoning. What runs with no key configured. |
| `openrouter` (recommended) | `OPENROUTER_API_KEY`, optionally `OPENROUTER_MODEL` (default `openai/gpt-4o-mini`) | One key in front of many providers/models — get one at https://openrouter.ai/settings/keys |
| `anthropic` | `ANTHROPIC_API_KEY`, optionally `ANTHROPIC_MODEL` | Direct Anthropic API |
| `openai` | `OPENAI_API_KEY`, optionally `OPENAI_MODEL` | Direct OpenAI API |

Set these in `infrastructure/.env` locally (the launcher's setup screen writes this file for you if
you used the `.exe`), or as real environment variables/secrets in your AWS task definitions in
production (`docs/DEPLOYMENT.md` B6's Terraform variables). No restart of the *frontend* is needed —
only the `api` and `agent-worker` processes read this at startup.

Every call is rate-limited per tenant *before* it reaches the provider
(`MODEL_RATE_LIMIT_PER_MINUTE`/`MODEL_RATE_LIMIT_PER_DAY`, defaults 20/2000) so a blocked call spends
zero tokens — tune these in `.env` if your real usage needs a different budget.

## 12. Model gateway resilience (circuit breaker)

Also in **Configuration Studio**, per tenant, no redeploy needed:

```bash
curl -X PUT http://localhost:8000/configuration/settings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"gateway_circuit_breaker_enabled": true, "gateway_timeout_seconds": 30, "gateway_failure_threshold": 3, "gateway_cooldown_seconds": 60}'
```

- **Call timeout** — how long one model call may take before it's treated as a failure.
- **Failure threshold** — consecutive failures (timeout, schema-invalid, provider error) before the
  breaker opens for this tenant.
- **Cooldown** — how long the breaker stays open (calls fail immediately, zero tokens spent) before
  allowing another attempt.

If your provider has its own SLA/latency profile, tune the timeout accordingly — a slow but reliable
provider wants a higher timeout and a higher failure threshold than a flaky one.

## 13. Real SSO / identity provider setup

**Local/demo**: `docker compose up` already brings up a real, spec-compliant Keycloak container
pre-loaded with a "reo" realm, a "reo-platform" client, and a demo user (`sso.demo` /
`Password123!`). Enable it per tenant and try it:

```bash
curl -X PUT http://localhost:8000/configuration/settings \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"sso_enabled": true}'
```

Then on the login screen, enter your tenant slug and click **Log in with SSO**. See
`docs/DEPLOYMENT.md`'s SSO section for the full walkthrough, including how to link SSO login into an
*existing* privileged account (by matching email) instead of provisioning a fresh Viewer.

**Connecting a real production IdP** (Cognito, Entra ID, Auth0, Okta, or your own Keycloak) instead
of the bundled one: this platform's OIDC client (`backend/app/routers/auth.py`) speaks the standard
authorization-code flow against *any* spec-compliant OIDC provider — swap these four settings to
point at yours, no code changes:

```bash
OIDC_ISSUER=https://your-idp.example.com/realms/your-realm        # server-to-server: token exchange + JWKS
OIDC_AUTHORIZE_URL_PUBLIC=https://your-idp.example.com/realms/your-realm/protocol/openid-connect/auth  # browser redirect target
OIDC_CLIENT_ID=your-registered-client-id
OIDC_CLIENT_SECRET=your-client-secret
OIDC_REDIRECT_URI=https://your-api-domain.example.com/auth/sso/callback   # must be registered as an allowed redirect URI on the IdP
FRONTEND_BASE_URL=https://your-dashboard-domain.example.com
```

Register `OIDC_REDIRECT_URI` as an allowed redirect URI in your IdP's client configuration, or the
authorization-code exchange will be rejected by the IdP itself. First login from a given IdP subject
(`sub` claim) auto-provisions a local account — linked by email if one already matches, otherwise
created fresh with the `viewer` role (promote it afterward via §2/§14 if it needs more).

## 14. RBAC roles reference

| Role | Can do |
|---|---|
| `viewer` | Read dashboard, decisions, audit |
| `operator` | + approve assigned items, acknowledge signals, bounded override |
| `senior_operator` | + four-eyes approval, pause operations, trigger e-stop |
| `portfolio_manager` | + manage objective policy/scenarios/constraints, ingest files, read economics |
| `ot_admin` | + manage adapters/command envelopes, read control readiness |
| `model_admin` | + manage model registry/eval, deploy models, trigger evaluation runs |
| `tenant_admin` | + manage users, settings (Configuration Studio), connectors, policies, activate connectors |
| `auditor_dpo` | + export evidence, read privacy data, manage data-subject requests |
| `platform_admin` | **Superuser** — every permission that exists, platform-wide, including cross-tenant break-glass reads (all audited) |

A user can hold multiple roles (`roles` is a list). Grant the minimum role that covers what someone
actually needs to do — `platform_admin` is meant for platform operators, not day-to-day tenant users.

## 15. Autonomy mode & governance

Policy Studio (`GET/PUT /governance/autonomy-policy`) controls how much authority the platform has
per scope (default `"portfolio"`, or a specific asset/site):

```bash
curl -X PUT http://localhost:8000/governance/autonomy-policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scope": "portfolio", "mode": "APPROVAL_REQUIRED", "max_action_risk": "low"}'
```

Modes, in increasing order of authority: `OBSERVE` (evidence only, no actions dispatched) →
`RECOMMEND` (actions proposed, none executed) → `APPROVAL_REQUIRED` (default; a human approves each
action in Approval Inbox) → `AUTONOMOUS_BOUNDED` (low-risk actions dispatch immediately, no human in
the loop). Moving to `AUTONOMOUS_BOUNDED` requires `safety_case_ref` in the same request — the API
returns `400` without one:

```bash
curl -X PUT http://localhost:8000/governance/autonomy-policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scope": "portfolio", "mode": "AUTONOMOUS_BOUNDED", "max_action_risk": "low", "safety_case_ref": "SC-2026-004"}'
```

An emergency stop is always available regardless of mode: `POST /governance/e-stop`.

## 16. Exporting audit evidence

`POST /exports` (Excel, streaming) or `GET /audit/evidence/{decision_id}` (a single decision's full,
hash-chained evidence pack) — both under **Audit & Exports**, requiring `export:evidence`
(Auditor/DPO or Platform Admin). Every export is itself audit-logged, and the underlying
`AuditEvent` table is hash-chained (`sha256(prev_hash + payload)`) so tampering is detectable.

## 17. Local demo vs. AWS production — what actually differs

| Concern | Local (`docker compose`) | AWS production (`docs/DEPLOYMENT.md` Part B) |
|---|---|---|
| Database | Postgres+TimescaleDB container | RDS PostgreSQL |
| Object storage | SeaweedFS S3 gateway | Real S3 + CloudFront |
| Secrets | `LocalFernetSecretsProvider` (env key) | Set `SECRETS_PROVIDER=aws` for `AwsSecretsManagerProvider` + real KMS |
| Frontend | Vite dev server | Static build behind CloudFront |
| SSO IdP | Bundled Keycloak | Point at your real IdP (§13) |
| Everything else (connectors, gateway, governance, exports) | Identical API surface | Identical API surface |

Nothing in §5–§16 above changes between the two — every command and endpoint is the same, just
against a different `http://localhost:8000` vs. your real API domain.

## 18. Troubleshooting

- **"connector cannot be activated"** — maker-checker: the user who created it can't also activate
  it. Use a second account.
- **A `database` connector 400s with an SSRF/host-not-allowed error** — see §5's allow-list note.
- **A different database engine** (MySQL, SQL Server, Oracle) — not supported by the `database`
  connector kind today (it's a plain `postgresql://` connection via SQLAlchemy). Export to CSV/JSON
  and use `data_table` (§6) instead, or extend `backend/app/ingestion/db_source.py`'s
  `rows_from_db_table()` for your engine (it's a small, isolated function).
- **Weather feed shows no effect** — confirm both `weather_site_lat`/`weather_site_lon` are set (the
  API rejects `live_weather_enabled: true` without them), and wait for the next decision cycle.
- **Circuit breaker seems "stuck" open** — it clears itself automatically after `cooldown_seconds`;
  lower it in Configuration Studio if that's too long for your testing.
- **SSO redirects to an error page** — check `OIDC_REDIRECT_URI` is registered exactly (including
  scheme/port) as an allowed redirect URI on the IdP side.
- **General API exploration** — `/docs` (Swagger UI) on any deployment lets you try every endpoint
  interactively with your bearer token.
