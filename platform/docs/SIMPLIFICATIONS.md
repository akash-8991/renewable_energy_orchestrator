# Simplifications — build vs the 8 spec documents

The 8 spec documents (BRD/PRD/FRD/TRD/Architecture/Infrastructure/Prompt-Agent-Design/
Implementation-Validation-Commercialisation) describe a multi-year enterprise engagement. This
build implements the full *functional* breadth of that spec with real, working logic in every
module, on a substitution set chosen to be buildable and runnable without a real client
engagement, real SCADA hardware, or a live legal/compliance program. Every substitution below is
deliberate and reversible — the interface it sits behind is the same interface the "real" AWS
component would sit behind, so swapping one in later is an infrastructure change, not a rewrite.

| Spec component | This build | Why | Swap path |
|---|---|---|---|
| EKS microservices (~15+ services) | FastAPI modular monolith (`api`) + isolated worker services (`optimizer-worker`, `agent-worker`, `ot-gateway-sim`, `edge-simulator`, `export-worker`) | Internal module boundaries already mirror the TRD §11 service list; running 15 separate repos/deployables adds ops overhead without adding capability at this stage | Split any module out of `api` into its own FastAPI app — it already only talks to others over the DB/event bus, never via in-process imports |
| MSK/Kafka + IoT Core/MQTT | Redis Streams (`reo_common/events.py`, consumer groups, DLQ, replay) | One dependency instead of three; consumer-group semantics are the same | `EventBus` is the only thing that would change; nothing else imports `redis` directly |
| Aurora PostgreSQL + Amazon Timestream | PostgreSQL + TimescaleDB extension (one engine, two jobs) | TRD's own stack table lists this as a valid combination | For AWS: RDS PostgreSQL doesn't support the timescaledb extension — either self-manage Timescale on EC2/ECS, or move `Telemetry`/`Forecast` to Timestream behind the same repository functions |
| 7+ AWS accounts (mgmt/log-archive/security/shared-services/non-prod/prod/client-prod) | One consolidated account, `platform/infra/terraform` | Multi-account is an AWS Organizations/landing-zone decision independent of the application; not applied this session regardless | Terraform Cloud/Organizations account-vending is additive infra, not an app change |
| EKS across 3 AZs | ECS Fargate across 3 AZs (`platform/infra/terraform/ecs.tf`) | Same resilience target, less cluster-ops surface for a first deployment | Same container images run on EKS with a different task-runner layer |
| Real IAM Identity Center/Cognito federation | JWT (local password) + `authlib` OIDC client wired but untested against a production IdP | No production IdP exists to federate against yet | Point `oidc_issuer`/`oidc_client_id` at any real IdP (Cognito, Entra ID, Keycloak) — the client code doesn't change |
| Real SCADA/EMS/BMS + OPC UA/IEC hardware | `ot-gateway-sim`: a separate, network-isolated service doing independent deterministic validation + simulated acknowledgement, with fault/latency injection | Doc 06 §8 explicitly says not to price/build real OT before a site survey and safety case exist — building against real hardware isn't possible or spec-compliant without a client site | Real OPC UA/MQTT client libraries are used in the adapter layer; only the thing they connect to changes |
| Formal OT hazard analysis + client-specific safety case | Not producible by software. The platform enforces a *structural* placeholder: no tenant/asset can be moved out of `APPROVAL_REQUIRED` mode without a recorded `safety_case_ref` on its autonomy policy | This is a real, external, client-and-site-specific process (doc 05 §7) | N/A — this gate is meant to force a real conversation, not be automated away |
| Legal DPIA / ROPA sign-off | Template document + the technical controls a DPIA would require (redaction, retention, DSR endpoints) — not a completed legal review | DPIA is a legal/organisational process, not a coding task | A DPO reviews and signs the template against a real tenant's data |
| IEC 62443 / NIS2 certification | A control-mapping document (which implemented control satisfies which clause) — not an audited certificate | Certification requires an external auditor | N/A |
| Maker-checker on every governance action | Implemented for Connector Studio activation (creator ≠ activator, enforced in `Connector.status` transitions) and high-risk approvals (`Approval.requires_second_approver`) | These are the two places the spec calls out four-eyes explicitly | Extend the same pattern to any other action by adding a `*_by` pair and a status-transition check |
| Real external IdP / SSO | OIDC client wired, testable against any dev IdP (e.g. free Keycloak/Auth0 tenant) | No production IdP to point at during this build | Set `oidc_issuer` etc.; no code change |

## Known tracked item: document intake (D3 multimodal ingestion)

`POST /ingestion/documents` reads scanned PDFs/photographed images via the same `ModelGateway`
vision path every specialist agent uses (`document_ingest.py`), but the following are deliberate,
documented limits rather than silent gaps:

- **No dedicated OCR fallback.** Extraction quality depends entirely on the underlying model's
  vision capability (Claude by default). There's no separate Tesseract/OCR pass to cross-check
  against — consistent with the rest of the agent layer's design (the model gateway *is* the
  reading mechanism), but worth knowing if a document is extremely low-quality.
- **Only solar/wind assets can receive a document-derived constraint.** The MILP takes a
  per-step generation cap for solar/wind (`forecast_kw[t]`) but batteries and the grid
  interconnection have their own, structurally different constraint handling that wasn't
  extended in this pass. `apply-constraint` refuses other asset types with a clear 400 rather
  than silently persisting a `Constraint` row the optimizer never reads.
- **Indirect prompt injection via the document image itself is not specially hardened beyond
  what the rest of the agent layer already does.** The system prompt instructs the model to treat
  in-image text as content to report on, not as instructions, and the extraction still goes
  through the same tool-forced, schema-validated, fail-closed path as any other agent call — but
  there's no separate image-level content-moderation pass. The bigger mitigation is architectural,
  not prompt-level: the extraction is evidence only, and a human must explicitly map a free-text
  asset reference onto a real `Asset` before anything derived from the document can affect a
  decision.
- **Real vision reasoning wasn't exercised against the live Anthropic API this session** — the
  local/demo deployment defaults to `MODEL_PROVIDER=mock` (no API key configured), same as every
  other agent. Verified via the mock gateway (schema-valid extraction, correct plumbing end to
  end: render → extract → persist → audit → apply → optimizer derate, confirmed against live DB
  data) and via unit tests; not verified against a real photographed document with real API calls.

## Known tracked item: frontend dependency advisories

`npm audit` flags moderate/high advisories in `vite`/`esbuild` (dev-server-only request forwarding,
not present in the production static build) and `react-router-dom` (an open-redirect variant and an
SSR-hydration deserialisation issue — this app has no SSR and builds no redirect target from user
input, so neither is reachable here). Both fixes require a major version bump (Vite 5→8, React
Router 6→7) outside the currently-declared semver ranges; `npm audit fix --force` applies them but
wasn't run this session so the bump could be verified properly (full re-test of routing and the dev
build) rather than shipped untested. Tracked here rather than silently ignored — bump both in a
dedicated follow-up pass.

## What is *not* simplified

Built with real logic end to end, calling live services where configured: ingestion (file/DB/
REST/streaming/multimodal PDF+image via vision), the digital twin, forecasting, the deterministic MILP optimizer + independent
feasibility validator, all specialist agents calling Anthropic Claude through a schema-validated
model gateway, the policy/safety engine's four autonomy modes, the OT gateway's independent
pre-dispatch revalidation, the append-only hash-chained decision/audit ledger, approvals, Connector
Studio (with real SSRF-hardened outbound HTTP to whatever endpoint a tenant admin configures),
Excel export, and multi-tenant isolation (enforced at the ORM layer, not just by convention).
