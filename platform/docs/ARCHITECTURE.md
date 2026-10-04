# Architecture — build to spec traceability

This maps what's implemented to where it's specified in the 8 client documents
(`renewable_energy_orchestrator/01..08_*.docx`). See `SIMPLIFICATIONS.md` for every deliberate
scope reduction, and the root plan for the full module-by-module build order. See
`PRODUCTION_READINESS_REVIEW.md` for a direct answer on deployment readiness and a consolidated
punch list — read that first if the question is "can this go live."

## Non-negotiable spine (present in every spec doc — must survive any future change)

```
edge-simulator ──publish──▶ reo.telemetry (Redis Stream)
                                   │
                                   ▼
                    api: ingestion → digital twin (canonical model, TRD §3)
                                   │
                    forecast/scenario ──▶ optimizer-worker (deterministic MILP, ADR-001)
                                   │              │
                                   │        independent feasibility validator (BR-03)
                                   │              │
                    agent-worker (9 agents, doc 07) ── evidence/explanation only, NEVER a command
                                   │
                    policy/safety engine ──▶ OBSERVE / RECOMMEND / APPROVAL_REQUIRED / AUTONOMOUS_BOUNDED
                                   │
                    Decision Ledger (append-only, versioned) ◀── every cycle writes here
                                   │
                    (if approval required) Approval Workflow
                                   │
                    ot-gateway-sim ── independent re-validation, immediately pre-dispatch (doc 05 §7)
                                   │
                    simulated SCADA ack ──▶ reconciliation ──▶ Decision Ledger outcome
```

Agents can only ever *reach* the optimizer/policy/OT layers through typed, schema-validated JSON —
there is no code path from `agent-worker` to `ot-gateway-sim` that does not pass through the policy
engine and (for anything but the lowest-risk bounded actions) a human approval.

### Continuous data flow and event-driven replanning

Two things start a decision cycle in `optimizer-worker`:

1. **The clock** — every `DECISION_CYCLE_SECONDS` (default 120; spec target 10 minutes).
2. **A data-change event** — the api publishes `reo.decision.cycle.requested` on the
   `reo.decision.cycle` stream (`reo_common.events.request_decision_cycle`) and the worker runs a
   cycle immediately, recording `trigger = "event:data_change"` on the Decision. After an
   event-driven cycle the scheduled cadence restarts from that moment.

What raises the event: `ingestion/connector_poller.py` re-reads every **active** `database`,
`data_table`, `iot` and `market_energy_purchase` connector every `CONNECTOR_POLL_SECONDS`
(default 60; 0 disables) through the same code path as a manual "Ingest now"
(`run_connector_ingest`). Each source is checked for *new* data — a SHA-256 content fingerprint plus,
for readings-shaped sources, a per-(asset, metric) `event_time` high-water mark (both kept in Redis);
a source with nothing new keeps being monitored but is not re-ingested, and only a source that brought
in new rows raises the event — after
waiting for the telemetry consumer to persist those rows, so the cycle plans on them. The
watched-folder scanner (`folder_watcher.py`, every 10 s) still ingests newly dropped files but does
not raise the event itself — a `data_table` connector pointing at the same file would otherwise
cause two cycles for one change. The event is only sent for a tenant that is `running`; an idle
tenant just ingests.

```
active connector / dropped file ──(every 60 s, only if content changed)──▶ reo.telemetry ──▶ Timescale
                                                       │
                                  (rows persisted) ────▼
                                  reo.decision.cycle ──▶ optimizer-worker: snapshot → forecast → solve → Decision
```

### Production hardening (summary)

Authentication re-reads the account on **every request** (`app/deps.py`): a signed token only proves
who someone was, so deactivating a user, changing their roles or removing their tenant applies
immediately. Sign-in is throttled in Redis and failures are audited; passwords have a minimum length;
responses carry security headers; the api refuses to start outside a local environment with development
secrets or wildcard CORS (`reo_common.config.production_config_problems`). Tenants onboard through
`database/bootstrap.py` and the Portfolio registry rather than the demo seed, and the optimizer plans
for **every** started tenant. Operational: `/ready`, healthchecks with worker heartbeats, restart
policies, AOF-persisted Redis with capped streams, Timescale retention on forecasts, non-root images.
Full list and what remains the operator's job: `docs/DEPLOYMENT.md` → *Production hardening checklist*.

### Forecasting: physics baseline + trained ML, operator-governed

Each asset's forecast is a physics-based baseline (diurnal solar curve, wind power curve, daily
demand/price shapes; `baseline-v1`) — and, additionally, a **trained ML model**
(`reo_common/forecast_ml.py`, `ml-ridge-v1`): a ridge regression on hour-of-day / day-of-year /
weekend features that takes the physics baseline as one of its inputs, trained on the telemetry the
platform has actually ingested (hourly means per asset; at least `min_training_hours` of history, one
week by default). Its uncertainty band is the model's own held-out residuals per hour of day. The
last 20% of history is held out so every model comes with an honest error figure *against the physics
baseline*. Training is closed-form least squares — deterministic, no random sampling.

**Operators decide what is used** (Policy Studio → *Forecast criteria*, `GET/PUT /forecasting/criteria`,
`POST /forecasting/criteria/accept`, `POST /forecasting/retrain`; permission `manage:forecast_criteria`,
held by operator, senior_operator, portfolio_manager, model_admin and tenant_admin; every decision is
audit-logged):

| Status | Meaning | What forecasts use |
|---|---|---|
| `proposed` (default) | nothing decided yet | physics baseline only — a newly trained model never takes over on its own |
| `accepted` | operator adopted the platform's proposal | `auto`: ML per asset only where it beats physics by ≥ `min_improvement_pct` (5%) |
| `custom` | operator set their own | their `model_mode` (`physics` / `ml` / `auto`), `band_scale` (uncertainty-band multiplier), `min_training_hours`, `min_improvement_pct`, `retrain_hours` |

The optimizer trains/refreshes models every `retrain_hours` (and 15 minutes after an
`insufficient_data` result) regardless of mode, so the evaluation is always there to inform the
decision. Each `Forecast` row is stamped with the `model_version` that produced it; an explicit `ml`
mode that has no usable model for an asset falls back to the baseline and marks the row `is_fallback`.

### Shocks are deterministic named scenarios

The six shocks (`CLOUD_COVER`, `WIND_SURGE`, `PRICE_SPIKE`, `BATTERY_OUTAGE`, `LINE_CONGESTION`,
`DEMAND_SHOCK`; `policy/scenarios.py`) are fixed, named perturbations, not Monte Carlo trajectories:
the same inputs under the same scenario always give the same plan, so an operator, approver or
auditor can reproduce and compare any "what if". Uncertainty is handled without sampling too — the
forecast's q10/q50/q90 band becomes one risk-adjusted deterministic series. `tests/test_scenarios_
deterministic.py` enforces it (exactly the six names, constant multipliers, no random imports in the
scenario modules, identical reruns).

### Maintenance is advisory only

The platform *recommends* maintenance; it never schedules, dispatches or enforces it. Advisories are
`Action` rows of type `maintenance_advice` (battery state of health < 80%, battery warranty cycles
< 500, or an asset whose source flags its telemetry bad — `policy/maintenance_advisory.py`), shown in
the Action Tickets page with status `advisory`. Enforced at every layer: no Signal is ever built for
one, no Approval is created (`requires_approval = false`), they don't change the optimizer's plan, and
the OT gateway refuses the command type outright. (Promoting an uploaded maintenance notice to a
capacity derate is a separate, explicit human decision, not something an advisory does.)

## Service → spec-document map

| Service | Primary spec sections | Canonical entities it owns |
|---|---|---|
| `backend` | FRD (all modules), TRD §11 (platform services), doc 05 §10 (platform architecture extension) | Tenant, User, Portfolio, Site, Asset, Battery, Decision, Action, Approval, Connector, CredentialRef, ExportJob, AuditEvent, DocumentIntake, AgentCallLog, AgentEvalRun |
| `policy` | TRD §4 (optimisation requirements), doc 05 ADR-001, hackathon problem 4 F1/F3 | reads Constraint/ObjectivePolicy/Forecast, writes Decision.plan, Action, ScenarioRun |
| `agent` | doc 07 (Prompt and Agent Design, in full) | writes Decision.reasoning, Decision.risk_flags, AgentCallLog, AgentEvalRun (also runs `eval_harness.py` on `reo.eval.request`) |
| `guardrails/ot-gateway-sim` | doc 05 §7 (Safety architecture), TR-OT-01 | writes Command, updates Signal.state |
| `infrastructure/edge-simulator` | BRD reference portfolio (5 solar/3 wind/2 BESS), TRD §7 (industrial protocols) | writes Telemetry |
| `output/export-worker` | FR-EXP-001/002, TR-EXP-01 | writes ExportJob, reads reporting projections |
| `frontend` | PRD §5/§9 (dashboard pages / platform workspaces) | — |
| `models` | TRD §3 (canonical model) | the canonical SQLAlchemy schema itself (`models/canonical.py`) — every table in the platform |
| `database` | TRD §3, FR-MT-001 (tenant isolation) | DB connection/session layer + tenant-scoping event listener (`database/connection.py`), Alembic migrations, seed script |
| `guardrails` | BR-03 (independent validator), TR-SSRF-01, TRD §5/§6 (agent guardrails) | validator.py, ssrf.py, rate_limit.py, pii.py — safety/validation logic, structurally separate from what it checks |
| `evaluation` | doc 07 (agent evaluation), TRD §5 (guardrail logging) | AgentCallLog/AgentEvalRun persistence + the fixed-scenario eval harness |
| `output` | FR-AU-001 (audit), FR-EXP-001/002 | the hash-chained audit log (`output/audit.py`) + Excel export worker |
| `packages/reo_common` | TR-AUTH-01, doc 07 (model gateway) | config, vendor-neutral model gateway (+ per-tenant circuit breaker, `platform_settings.py`), Redis event bus, JWT/RBAC + real OIDC/SSO auth (`backend/app/routers/auth.py`'s `/auth/sso/*` against the bundled Keycloak container), secrets vault, digital-twin helpers — cross-cutting code every service above depends on |

## Tenant isolation (BR-07 / FR-MT-001)

Defense in depth, not a single control: every tenant-scoped SQLAlchemy model is registered with a
`do_orm_execute` event listener (`reo_common/db.py`) that injects a mandatory `tenant_id` predicate
into every SELECT via `with_loader_criteria`, keyed off a per-request `ContextVar` set from the JWT.
A query that forgets to filter by tenant still cannot leak another tenant's rows — with no tenant
context set, it fails closed (returns nothing) rather than open. Platform-admin cross-tenant reads
must explicitly opt into `break_glass_cross_tenant()`, which is meant to be paired with an
`AuditEvent` write by the caller (FR-PLT-002).

## Agent layer (doc 07)

Every specialist agent calls `ModelGateway.complete_structured(...)` with a Pydantic response model.
The gateway forces the underlying model (OpenRouter by default) to emit exactly that JSON
shape via tool-forcing, validates server-side, retries once, then fails closed
(`GatewayError`) — a schema-invalid response is never passed through as if it were valid evidence,
and the same retry-then-fail-closed contract applies to *any* call failure, not just an
invalid response: a provider-level error (rate limit, billing, auth, network, 5xx) is caught and
converted to `GatewayError` too, so agent/worker.py's per-agent `except GatewayError` — which marks
that one agent INSUFFICIENT_EVIDENCE and lets every other agent in the pass still run — always gets
the chance to do so, rather than the raw provider exception escaping uncaught and aborting the
whole decision's reasoning before anything is committed.
See `agent/agents/` for the 9 specialist agents (data_quality, forecast, asset, market,
grid, optimisation_reviewer, risk_critic, governance, explanation) and the exact prompts from doc 07
§2-§5. The 10th role in doc 07 §3, "Orchestrator", is implemented as the deterministic pipeline code
in `cycle.py`/`worker.py` rather than a further LLM call — see `agents/base.py`'s docstring for why.

## API surface → dashboard workspace map

| Workspace (web) | Primary endpoints |
|---|---|
| Portfolio Operations | `GET/POST /operations/{status,start,stop}` (start/stop gate), `GET /twin/portfolio`, `GET /twin/batteries`, `GET /twin/assets/{id}/telemetry`, `GET /twin/trend` (chart), `GET /decisions` (recent-decisions summary, `limit=5`) — the twin/trend/decisions calls only run once `operating_state=running` |
| Decision Centre | `GET /decisions`, `GET /decisions/{id}`, `GET /decisions/{id}/scenario-runs` |
| Customers | `GET /customers`, `GET /customers/filters`, `GET /customers/{ref}/insights` (retail accounts); `GET /twin/portfolio` (asset picker), `GET /actions?asset_id=`, `GET /decisions/{id}` (governed-asset decisions) |
| Approval Inbox | `GET /governance/approvals`, `POST /governance/approvals/{id}/decide` |
| Live Signal Monitor | `GET /signals` |
| Action Tickets | `GET /actions` |
| Connector Studio | `GET/POST /connectors` (incl. `kind`: generic/market_energy_purchase/scada/iot/database/data_table), `POST /connectors/{id}/{test,activate,disable,ingest}` (`ingest`: `data_table`/`database` route recognized reference-dataset files/tables through `hackathon_dataset.py`'s canonical mapping else the generic telemetry shape; `market_energy_purchase` writes a real price `Forecast` series; `iot` publishes real telemetry through the same generic shape; `generic`/`scada` remain registration/reachability-test only, the latter permanently by design — see `SIMPLIFICATIONS.md`) |
| Configuration Studio | `GET/PUT /configuration/settings` (`manage:settings`/`manage:platform_config`) — live weather feed, model gateway circuit breaker thresholds, SSO enablement, all per-tenant and live-editable with no redeploy |
| Policy Studio | `GET/PUT /governance/autonomy-policy`, `GET/PUT /governance/objective-policy`, `POST /governance/e-stop`, `GET/PUT /forecasting/criteria`, `POST /forecasting/criteria/accept`, `POST /forecasting/retrain` (forecast criteria: accept the proposal or set your own) |
| Simulation Lab | `GET/PUT /simulation/scenario`, `POST /simulation/scenario/reset` |
| Agent Observability | `GET /observability/summary`, `GET /observability/agent-calls`, `GET/POST /observability/eval-runs[/run]` |
| Audit & Exports | `GET /audit/events`, `GET /audit/evidence/{decision_id}`, `POST /exports`, `GET /exports/{id}` (Excel export includes an "Actions" sheet) |
| Tenant Administration | `GET/POST /admin/users`, `PATCH /admin/users/{id}` (deactivate/roles/password reset), `GET/POST /admin/tenants`, and the portfolio registry: `GET/POST /admin/portfolio/{sites,assets}`, `PATCH /admin/portfolio/assets/{id}` (`manage:assets`) |
| Platform Operations | rollups over the above; no dedicated endpoint |
| — (all pages) | `POST /auth/login`, `GET /auth/me` |

**Document Intake — API-only, no dashboard workspace.** Removed from the sidebar by request; the
real ingestion endpoints behind it are untouched and still fully functional, just API-only now (see
`docs/USAGE_GUIDE.md` §6 for the curl walkthrough). Multi-file upload, routed per extension: `POST
/ingestion/documents` (.pdf/.png/.jpg/.jpeg via vision, .docx via extracted text — same agent/schema
either way) and `POST /ingestion/files` (.csv/.json/.xlsx/.xlsm) — both ingest real data but
deliberately do *not* auto-start the tenant (only an active `database`/`data_table` connector in
Connector Studio does that). `/ingestion/files` tries, in order: the fixed
asset_id/metric/event_time/value/unit shape, a recognized reference-dataset filename, an
already-approved `TableMappingRule` for this exact column layout (no model call), then the generic
mapping agent as a last resort, which produces a `DataMappingProposal` for human review rather than
ingesting on its own say-so. Also `GET /ingestion/documents`, `POST
/ingestion/documents/{id}/apply-constraint`, `GET /ingestion/mapping-proposals`, `POST
/ingestion/mapping-proposals/{id}/{approve,reject}` — this last pair (reviewing/approving a pending
mapping proposal) has no UI anywhere else either now that the page is gone, so it's API-only too.

## Decision cycle (FRD §3.1)

1. Scheduler/event detector opens a decision cycle with a correlation ID.
2. Data-quality agent assesses the trusted snapshot; twin state is locked.
3. Forecast + scenario services produce trajectories for the horizon. Forecasts come from the
   physics-based baseline and/or a trained ML model per asset, as the operator's forecast criteria
   dictate (see *Forecasting* below); scenarios are the six deterministic named shocks.
4. Optimizer computes a feasible plan; the independent validator re-checks it.
5. A risk-averse alternative is solved against the conservative tail of the forecast band, and a
   forward-looking comparison across all six named scenarios is solved and persisted as
   `ScenarioRun` rows (`scenario_lab.py`) — before anything happens for real.
6. Governed `Action` rows are created for every action family the plan actually uses — battery
   charge/discharge, grid buy/sell, renewable curtailment, demand response (`actions_builder.py`) —
   not just batteries.
7. Risk/Critic agent challenges the plan.
8. Policy/safety engine assigns disposition per the tenant's autonomy mode.
9. Decision Ledger persists the versioned record with full evidence.
10. If approval-gated, the Approval Workflow waits for a valid, unexpired approval.
11. `ot-gateway-sim` independently re-validates immediately pre-dispatch, then simulates execution.
12. Telemetry reconciliation measures the actual outcome; KPIs and audit update.

## Hackathon problem 4 alignment (F1/F2/F3)

Three gaps identified against the ET AI Hackathon's Problem 4 solution-feature ladder were closed
after the initial build (see memory/session history for the full gap analysis):

- **F1** ("determine optimal cluster of actions") was already met; `test_actions_builder.py`'s
  `test_a_full_coordinated_cycle_produces_one_action_per_family` is the regression test for it.
- **F2** ("optimal cluster of options after determining optimality criteria... adjusting for
  uncertainty") needed a way to actually *set* the optimality criteria, not just have the optimizer
  apply a fixed set from seed time — `GET/PUT /governance/objective-policy` (Policy Studio) now
  lets a portfolio_manager change the weighted objective, carbon price, and risk-aversion at
  runtime, versioned so past decisions keep the criteria that governed them.
- **F3** ("simulate situation/scenario over a period of time accounting for potential variations")
  needed a genuine forward-looking what-if capability, not just the Simulation Lab's live shock
  injection into the real-time simulator. `scenario_lab.py` re-solves the same 24h horizon under
  each of the six named scenarios every cycle and persists the comparison (`ScenarioRun`), surfaced
  in the Decision Centre drawer's "Scenario Comparison" tab.

## Hackathon problem 4 alignment (D3: multimodal ingestion)

D1/D2 (structured/textual input) were met by the original build's CSV/JSON/XLSX telemetry and
`data/`-folder ingestion. D3 ("highly heterogeneous multimodal input") was not: nothing in the
platform could read a scanned/photographed PDF or image, despite the BRD listing images/PDF as
in-scope file types. Closed as its own ingestion path rather than bolted onto the telemetry one,
because a maintenance notice or storm alert doesn't decompose into asset_id/metric/value rows —
it has to be *read*:

- `POST /ingestion/documents` (Document Intake, API-only — see above) accepts a PDF or image. `backend/app/
  ingestion/document_ingest.py` renders each page to a PNG (`pypdfium2` for PDF pages, `Pillow` for
  photos) and shows it to the same `ModelGateway.complete_structured(...)` every specialist agent
  already uses — extended with an `images` parameter (`model_gateway.py`) so Anthropic's/OpenAI's
  vision input rides the identical tool-forced-schema, fail-closed path (`GatewayError` on a
  schema-invalid read, same as any other agent) rather than a separate, unvalidated OCR pipeline.
- The extraction (`DocumentExtraction`: document_type, summary, affected asset refs *as written on
  the page*, effective window, severity, capacity impact, confidence, a verbatim excerpt) is
  persisted as a `DocumentIntake` row — evidence, not a decision. Consistent with the non-negotiable
  spine (agents produce evidence, never commands), it never becomes a `Constraint` on its own.
- A human with `manage:constraints` (portfolio_manager) reviews it and, for a real asset outage/
  derate, explicitly maps the document's free-text asset reference onto an actual tenant `Asset` and
  promotes it (`POST /ingestion/documents/{id}/apply-constraint`) into a real `Constraint`
  (`capacity_derate_from_document`). `cycle.py` reads that Constraint on the *next* decision cycle
  and derates the affected solar/wind asset's per-step forecast (`document_constraints.py`,
  unit-tested in `test_document_constraints.py`) — so an uploaded document is a genuine input to the
  optimizer's next plan, not just something a human can read in a UI.
- Scoped honestly to solar/wind: the endpoint refuses to apply a document-derived constraint to a
  battery or the grid interconnection, because the MILP only takes a time-varying capacity cap for
  generation assets (`forecast_kw[t]`) — batteries and the grid have their own, structurally
  different constraint handling that this change doesn't extend. Persisting an inert `Constraint`
  row for those asset types would look "applied" in the UI while doing nothing, which is worse than
  refusing outright. See `SIMPLIFICATIONS.md` for what this does and doesn't cover.
