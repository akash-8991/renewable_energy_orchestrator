# Architecture — build to spec traceability

This maps what's implemented to where it's specified in the 8 client documents
(`renewable_energy_orchestrator/01..08_*.docx`). See `SIMPLIFICATIONS.md` for every deliberate
scope reduction, and the root plan for the full module-by-module build order.

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
                    agent-worker (10 agents, doc 07) ── evidence/explanation only, NEVER a command
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
| `apps/api` | FRD (all modules), TRD §11 (platform services), doc 05 §10 (platform architecture extension) | Tenant, User, Portfolio, Site, Asset, Battery, Decision, Action, Approval, Connector, CredentialRef, ExportJob, AuditEvent |
| `apps/optimizer-worker` | TRD §4 (optimisation requirements), doc 05 ADR-001 | reads Constraint/ObjectivePolicy/Forecast, writes Decision.plan |
| `apps/agent-worker` | doc 07 (Prompt and Agent Design, in full) | writes Decision.reasoning, Decision.risk_flags |
| `apps/ot-gateway-sim` | doc 05 §7 (Safety architecture), TR-OT-01 | writes Command, updates Signal.state |
| `apps/edge-simulator` | BRD reference portfolio (5 solar/3 wind/2 BESS), TRD §7 (industrial protocols) | writes Telemetry |
| `apps/export-worker` | FR-EXP-001/002, TR-EXP-01 | writes ExportJob, reads reporting projections |
| `apps/web` | PRD §5/§9 (dashboard pages / platform workspaces) | — |
| `packages/reo_common` | TRD §3 (canonical model), TRD §5/§6 (agent guardrails), TR-AUTH-01/SSRF-01 | shared models, tenancy enforcement, model gateway, secrets provider |

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
The gateway forces the underlying model (Anthropic Claude by default) to emit exactly that JSON
shape via tool-forcing, validates server-side, retries once, then fails closed
(`GatewayError`) — a schema-invalid response is never passed through as if it were valid evidence.
See `apps/agent-worker/agents/` for the 10-agent roster and the exact prompts from doc 07 §2-§9.

## Decision cycle (FRD §3.1)

1. Scheduler/event detector opens a decision cycle with a correlation ID.
2. Data-quality agent assesses the trusted snapshot; twin state is locked.
3. Forecast + scenario services produce trajectories for the horizon.
4. Optimizer computes a feasible plan; the independent validator re-checks it.
5. Risk/Critic agent challenges the plan.
6. Policy/safety engine assigns disposition per the tenant's autonomy mode.
7. Decision Ledger persists the versioned record with full evidence.
8. If approval-gated, the Approval Workflow waits for a valid, unexpired approval.
9. `ot-gateway-sim` independently re-validates immediately pre-dispatch, then simulates execution.
10. Telemetry reconciliation measures the actual outcome; KPIs and audit update.
