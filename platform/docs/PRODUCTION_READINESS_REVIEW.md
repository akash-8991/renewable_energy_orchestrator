# Production readiness review — 2026-09-26

Scope: (1) verify current coverage against the 8 client spec documents and the ET AI Hackathon
"Agentic Edition" Problem 4 grid, and give a direct answer on production-deployment readiness
*assuming an Anthropic API key is supplied*; (2) audit and close four concrete gaps — UI
responsiveness, agent observability/evaluation, live-system connector coverage, and per-action
audit tickets. Every claim below was checked against the running code/stack, not assumed — see
`git log` for the commits this maps to.

## 1. Direct answer: is this production-ready if an Anthropic key is provided?

**No — dropping in `ANTHROPIC_API_KEY` flips the agent layer from schema-valid-but-generic mock
reasoning to genuine LLM reasoning, and that is a real, load-bearing upgrade (every specialist
agent, the governance/risk/explanation agents, and the D3 document-vision path all become
materially more useful). But it does not address anything outside the model call itself.** The
gap between "runs a full, correct decision cycle end-to-end on Docker Compose" (true today) and
"safe to operate against a real portfolio" is entirely made of things an API key can't fix:

| Category | Status today | What's actually missing |
|---|---|---|
| Core decision/control loop | **Real.** Optimizer, independent validator, policy engine, OT gateway, decision ledger, approvals all run real logic against real (simulated) data. | Nothing — this is the part of the spec that's genuinely done. |
| Agent reasoning quality | Schema-valid, zero-reasoning (`MockModelGateway`) without a key. | An Anthropic key. Also: no prompt-injection red-team pass, no adversarial eval corpus beyond the 3 behavioral cases added this pass (§4 below), no per-agent cost/latency budget or circuit breaker if Anthropic is slow/down mid-cycle (a stuck agent call currently just makes that decision cycle slow — there's no timeout/circuit-breaker around `complete_structured`). |
| Real-world data ingestion | CSV/JSON/XLSX + PDF/image (D3, this session) ingestion is real. | **No live feed is wired into the pipeline.** Forecast prices are a synthetic sine wave (`forecast.py`); Connector Studio (§4 below) can *register* a market-data/database/SCADA endpoint but nothing reads from one yet. Going live needs an actual scheduled-pull or push-ingestion path from a real market/weather/SCADA source into `Forecast`/`Telemetry`. |
| Real hardware | Simulated throughout (`edge-simulator`, `ot-gateway-sim`) — deliberate per doc 06 §8 ("don't price/build real OT before a site survey"). | A real site survey, a formal OT hazard analysis and safety case (the platform enforces *recording* a `safety_case_ref` before autonomy — it can't manufacture the analysis itself), real OPC UA/SCADA hardware and a commissioning/HIL test pass. |
| Cloud deployment | Terraform written (`infrastructure/terraform/*.tf` — VPC, ECS Fargate, RDS, ElastiCache, S3+CloudFront, Secrets Manager, ALB), `terraform validate`-clean, step-by-step apply instructions in `docs/DEPLOYMENT.md` Part B, **never `apply`d**. | An actual AWS account, a real `terraform apply`, then load testing against that environment (nothing here has been tested under concurrent load — the whole verification history is single-tenant, low-QPS). |
| CI/CD | Real: `.github/workflows/ci.yml` runs lint (ruff), the full pytest suite (54 tests) against a migrated Postgres, builds every service image, and runs `demo_runner.py`'s full 10-step script against a live `docker compose up` stack on every push/PR. | No CD (nothing auto-deploys anywhere), no canary/blue-green story, no automated DB backup/restore drill. |
| Auth | JWT + local password, `authlib` OIDC client wired but **never tested against a real IdP** (no production IdP exists to point at). | A real IdP (Cognito/Entra/Keycloak) and an actual federation test. |
| Secrets | `LocalFernetSecretsProvider` (Fernet symmetric encryption, env-derived key) is what's actually running; `AwsSecretsManagerProvider` exists in code but is unexercised. | KMS-backed secrets in a real AWS account; a key-rotation runbook (rotating `JWT_SECRET` today breaks stored SSO secrets — documented, not fixed). |
| Dependency hygiene | Backend deps current. `npm audit` flags moderate/high advisories in `vite`/`esbuild` (dev-server-only, not in the prod static build) and `react-router-dom` (two advisories, neither reachable — no SSR, no user-controlled redirect target). Documented in `SIMPLIFICATIONS.md`, not fixed (needs a major-version bump, tracked as a follow-up). | The version bump + a full regression pass on routing. |
| Legal/compliance | DPIA/ROPA template + the technical controls a DPIA would require exist. IEC 62443/NIS2 control-mapping doc exists. | An actual DPO sign-off and an actual external audit — neither is a coding task. |

**Bottom line:** an API key makes the *demo* genuinely convincing (real agent reasoning instead of
scaffolding). It does not make this *deployable against a real grid, real money, or real
customer data* — that gate is the AWS apply + real connector wiring + safety case + IdP + audit,
none of which are software-only tasks left undone by oversight; they're the things this session's
own `SIMPLIFICATIONS.md` has said from the start require a real client engagement to close.

## 2. Problem statement (hackathon Problem 4) coverage

Unchanged from the 2026-09-25 pass except D3, closed this session:

| Axis | Status | Evidence |
|---|---|---|
| D1/D2 (structured/textual input) | Met | CSV/JSON/XLSX + REST/file/folder ingestion, `test_ingestion.py`. |
| **D3 (heterogeneous multimodal input)** | **Met, this session** | `backend/app/ingestion/document_ingest.py` — PDF/image → vision extraction → `DocumentIntake` evidence → human-promoted `Constraint` that the optimizer genuinely derates solar/wind generation against (`document_constraints.py`, verified against live DB data: a 36,661kW forecast peak clipped to 12,000kW). |
| F1 (optimal action cluster) | Met | `test_actions_builder.py::test_a_full_coordinated_cycle_produces_one_action_per_family`. |
| F2 (optimality criteria, adjustable) | Met | `GET/PUT /governance/objective-policy`, versioned. |
| F3 (scenario simulation over time) | Met | `scenario_lab.py` re-solves all 6 named scenarios every cycle, persisted as `ScenarioRun`. |

All three D-axis tiers and all three F-axis tiers now have real, verified implementations — this
is the first point in the build where the platform's *declared* solution-grid position and its
*actual* code both land on D3/F3 without a documented shortfall on either axis.

## 3. This session's four additional tasks

### UI responsiveness (mobile + desktop)

Was **completely unaddressed** before this session — `frontend/src/styles.css` had zero `@media`
queries, and the 230px sidebar was permanently docked (over 60% of a 375px phone screen). Fixed:

- `styles.css`: a single `@media (max-width: 860px)` block turns the sidebar into an off-canvas
  drawer, shrinks content padding, and sets `.content { overflow-x: auto }` so any page's table
  (kept at a readable `min-width: 640px` on mobile) scrolls horizontally instead of breaking the
  layout — this covers every existing table-based page without editing each one individually.
- `Layout.tsx`: hamburger toggle + dimmed overlay + auto-close on route change.
- Verified live at 375×812 (iPhone-class) and desktop widths — cards, forms, drawer, and
  horizontal table scroll all confirmed working via the actual browser, not just CSS review.

**Not done:** a systematic per-page mobile pass (e.g. `PolicyStudio.tsx`'s multi-slider forms,
`DecisionCentre.tsx`'s drawer) beyond what the global layout/table fix already covers — those
render usably (verified for Document Intake, Decision Centre, Portfolio Operations) but weren't
individually redesigned for touch ergonomics.

### Agent observability + evaluation dashboard

**Was a bigger gap than it looked**: `MODEL_ADMIN`'s three permissions
(`manage:model_registry`/`manage:model_eval`/`deploy:model`) existed in `security.py`'s RBAC
catalogue with **zero endpoints or UI behind any of them** — a role nobody could actually use.
Built:

- `AgentCallLog`: every `ModelGateway.complete_structured(...)` call (every specialist agent, the
  document-intake vision path) now persists agent, provider/model, latency, real token usage
  (previously discarded even from the successful-call path), schema-validity and retry outcome,
  via an opt-in `on_call_record` hook so `model_gateway.py` itself stays DB-agnostic and every
  existing mock-gateway test keeps working untouched.
- `GET /observability/summary` / `/agent-calls` — real call-volume/latency/schema-validity
  metrics, verified live showing calls from both the ordinary decision-cycle traffic and the eval
  harness.
- A small, honest evaluation harness (`agent/eval_harness.py`): 6 fixed cases across
  3 agents, split into **structural** (schema-valid — true under mock too) and **behavioral**
  (requires real reasoning — e.g. "must raise a HIGH finding when 100% of telemetry is stale").
  Behavioral cases are reported **skipped**, not failed, when the active gateway is mock, so the
  dashboard never shows a misleading 0%-pass regression that's actually just demo-mode.
  Triggering (`POST /observability/eval-runs/run`) publishes onto a new Redis stream
  (`reo.eval.request`) consumed by `agent-worker`, matching the platform's existing
  cross-service event pattern rather than duplicating agent code into the api container.
- New "Agent Observability" dashboard page (Call Metrics + Evaluation tabs), verified live.

**Not done, and said so on the page itself:** this is a demonstration-scale eval suite (6 cases,
3 agents), not a comprehensive one. A real eval program needs a much larger labelled corpus,
statistical significance over repeated runs, and ideally human/rubric scoring for the qualitative
findings — none of which a coding session can manufacture without real domain-expert-labelled data.

### Live-system connectors (database / SCADA / market)

Connector Studio (`GET/POST /connectors`, `/{id}/test`, `/activate`, `/disable`) already existed
and is genuinely solid as a *registration* surface — SSRF-hardened (DNS re-resolved at test time,
blocks loopback/link-local/metadata), Fernet-vaulted credentials never returned in cleartext,
maker-checker enforced (creator ≠ activator). **What it never did: connect to anything.** Grepping
the whole codebase confirmed zero non-router code path reads a `Connector` row — `forecast.py`'s
price series is a synthetic sine wave, `ot-gateway-sim` has no reference to `Connector` at all.

Fixed the honest part of this gap: added a `kind` field (`generic` / `market_energy_purchase` /
`scada` / `iot` / `database` / `data_table`) so the page is now explicitly organised around these
use cases, with inline UI copy that states plainly what's validated (SSRF + HTTP reachability)
versus what isn't (no live pipeline wiring yet) for each kind. **Deliberately did not** fake a
deeper integration for five of the six — adding real live-price ingestion, a real DB query path,
or real OPC-UA dispatch through this UI would each be its own multi-day feature (scheduled
polling/caching, protocol-specific validation, safety review for anything touching OT), not a
same-session bolt-on, and `scada` never will connect to real dispatch regardless of effort
available — doc 05's architecture keeps that on `ot-gateway-sim` exclusively, by design. A later
session did wire the sixth, `data_table`: `POST /connectors/{id}/ingest` fetches and parses its
`endpoint_url` as telemetry, the same validated path a file upload goes through — see
`SIMPLIFICATIONS.md`'s "Portfolio-wide start/stop gate" section for the fuller picture (this also
now gates the dashboard/decision cycle behind an explicit Start, requiring a connected data source
first).

### Per-action ticket log, exportable

**Also a real gap**: `Action` rows (the governed, risk-tiered output of `actions_builder.py` —
battery charge/discharge, grid buy/sell, curtailment, demand response) were persisted correctly
but **never exposed by any API response** — not even inside `GET /decisions/{id}`, which only
returns the raw solver plan JSON and agent reasoning. There was no way to see "every action the
platform has ever proposed" anywhere in the UI. Fixed:

- `GET /actions` — every `Action`, joined with its `Decision` (cycle/status), latest `Signal`
  (dispatch state) and latest `Approval` (outcome), collapsed into one `ticket_status`
  (`pending`/`dispatching`/`dispatched`/`rejected`/`failed`/`held`/`expired`/...), filterable by
  status/type/risk/date.
- New "Action Tickets" dashboard page with client-side CSV export (immediate, no export-job
  round-trip) — verified against real governed "sell" actions from live decision cycles.
- Added the "Actions" sheet the existing Excel evidence-pack export (`export-worker`) was
  conspicuously missing (it already loaded the `actions` query result and simply never wrote it to
  a sheet) — verified by downloading a real export and checking the sheet's rows.

## 4. Consolidated punch list for a real production push

Roughly in the order a real deployment would need them:

1. **AWS apply** — provision the already-`validate`-clean Terraform for real (steps in
   `docs/DEPLOYMENT.md` Part B), then load-test it.
2. **A live market-data / weather / grid feed wired into `forecast.py`**, replacing the synthetic
   diurnal model — this is the single highest-leverage gap for genuine production usefulness.
3. **Real SCADA/OT hardware integration**, gated by an actual site survey and safety case — not
   something to shortcut regardless of engineering effort available.
4. **A real external IdP** federation test (OIDC client is wired, never exercised against one).
5. **Dependency version bump** (`vite`/`react-router-dom`) with a full regression pass.
6. **A circuit breaker / timeout budget around `ModelGateway.complete_structured`** so a slow or
   down Anthropic API degrades a decision cycle gracefully instead of just running long.
7. **Expand the eval harness** with a real labelled corpus once real usage data exists to draw one
   from.
8. **Wire a `market_energy_purchase`/`database`/`iot` Connector into the actual data path** it's
   now explicitly categorised for, once one is registered against a real endpoint (`data_table` is
   done; `scada` is deliberately excluded from this list — see above).

None of these are hidden — each is either already flagged in `SIMPLIFICATIONS.md` or added there/
here by this review. The platform's own standing practice (documented in every prior commit
message and in `ARCHITECTURE.md`/`SIMPLIFICATIONS.md`) is to state exactly this kind of boundary
rather than claim a simulated system is production-complete, and this review continues that.
