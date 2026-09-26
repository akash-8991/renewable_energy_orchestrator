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
| `packages/reo_common` | TR-AUTH-01, doc 07 (model gateway) | config, vendor-neutral model gateway, Redis event bus, JWT/RBAC auth, secrets vault, digital-twin helpers — cross-cutting code every service above depends on |

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
| Connector Studio | `GET/POST /connectors` (incl. `kind`: generic/market_energy_purchase/scada/iot/database/data_table), `POST /connectors/{id}/{test,activate,disable,ingest}` (`ingest`: `data_table` (URL or local watched-folder path) and `database` (`postgresql://` connection string + table name) fetch/query and route recognized reference-dataset files/tables through `hackathon_dataset.py`'s canonical mapping, else the generic telemetry shape) |
| Policy Studio | `GET/PUT /governance/autonomy-policy`, `GET/PUT /governance/objective-policy`, `POST /governance/e-stop` |
| Simulation Lab | `GET/PUT /simulation/scenario`, `POST /simulation/scenario/reset` |
| Agent Observability | `GET /observability/summary`, `GET /observability/agent-calls`, `GET/POST /observability/eval-runs[/run]` |
| Audit & Exports | `GET /audit/events`, `GET /audit/evidence/{decision_id}`, `POST /exports`, `GET /exports/{id}` (Excel export includes an "Actions" sheet) |
| Tenant Administration | `GET/POST /admin/users`, `GET/POST /admin/tenants` |
| Platform Operations | rollups over the above; no dedicated endpoint |
| Document Intake | Multi-file upload, routed per extension: `POST /ingestion/documents` (.pdf/.png/.jpg/.jpeg via vision, .docx via extracted text — same agent/schema either way) and `POST /ingestion/files` (.csv/.json/.xlsx/.xlsm) — both ingest real data but deliberately do *not* auto-start the tenant (see Portfolio Operations — only an active `database`/`data_table` connector in Connector Studio does that). `/ingestion/files` tries, in order: the fixed asset_id/metric/event_time/value/unit shape, a recognized reference-dataset filename, an already-approved `TableMappingRule` for this exact column layout (no model call), then the generic mapping agent as a last resort, which produces a `DataMappingProposal` for human review rather than ingesting on its own say-so. Also `GET /ingestion/documents`, `POST /ingestion/documents/{id}/apply-constraint`, `GET /ingestion/mapping-proposals`, `POST /ingestion/mapping-proposals/{id}/{approve,reject}` |
| — (all pages) | `POST /auth/login`, `GET /auth/me` |

## Decision cycle (FRD §3.1)

1. Scheduler/event detector opens a decision cycle with a correlation ID.
2. Data-quality agent assesses the trusted snapshot; twin state is locked.
3. Forecast + scenario services produce trajectories for the horizon.
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

- `POST /ingestion/documents` (Document Intake workspace) accepts a PDF or image. `backend/app/
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
