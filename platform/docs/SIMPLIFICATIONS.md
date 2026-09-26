# Simplifications — build vs the 8 spec documents

See `PRODUCTION_READINESS_REVIEW.md` for the consolidated, prioritised version of everything below
plus a direct production-readiness verdict — this file is the detailed reference it summarises.

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
| 7+ AWS accounts (mgmt/log-archive/security/shared-services/non-prod/prod/client-prod) | One consolidated account, `platform/infrastructure/terraform` | Multi-account is an AWS Organizations/landing-zone decision independent of the application; not applied this session regardless | Terraform Cloud/Organizations account-vending is additive infra, not an app change |
| EKS across 3 AZs | ECS Fargate across 3 AZs (`platform/infrastructure/terraform/ecs.tf`) | Same resilience target, less cluster-ops surface for a first deployment | Same container images run on EKS with a different task-runner layer |
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

## Known tracked item: Connector Studio is a registration surface for four of six kinds

`GET/POST /connectors` (SSRF-hardened, vaulted credentials, maker-checker) categorises a
connector's `kind` (`generic`/`market_energy_purchase`/`scada`/`iot`/`database`/`data_table`).
Four of the six still only do what they always did: store config, run a reachability test, and
gate activation. Grepping the codebase confirms nothing outside `routers/connectors.py` reads a
`Connector` row for these kinds — `forecast.py`'s price series is a synthetic diurnal model, and
`ot-gateway-sim` has no reference to `Connector` at all. Wiring a `market_energy_purchase`
connector into `forecast.py`'s price forecast, or an `iot` connector into replacing the
edge-simulator's synthetic sensor feed, is each a distinct, non-trivial feature (scheduled
polling/caching, protocol-specific validation) — deliberately not attempted as a same-session
bolt-on, and **`scada` specifically never will be**: doc 05's non-negotiable architecture keeps
OT command dispatch on the independent, safety-critical `ot-gateway-sim` path exclusively, so a
user-registered SCADA connector deliberately has no route into real command execution regardless
of its activation status, by design, not by omission.

The other two kinds are genuine, working data paths, not registration-only:

- `data_table`: `POST /connectors/{id}/ingest` (an active connector only) fetches its
  `endpoint_url` — an http(s) URL, *or* a path under the platform's watched local data folder
  (`DATA_WATCH_DIR`, the same read-only mount the background folder-watcher scans) — and
  parses+publishes it as real telemetry, through the identical `parse_telemetry_file()`/
  `publish_readings()` path a CSV/JSON/XLSX file upload already goes through.
- `database`: `endpoint_url` is instead a `postgresql://` connection string, egress-checked the
  same way an HTTP endpoint is (`guardrails/ssrf.py`'s `check_outbound_host`) but restricted to an
  explicit allow-list (currently just the reference `source-db` container — see
  `infrastructure/docker-compose.yml` — a real deployment would let a tenant admin maintain this
  list). `POST /connectors/{id}/ingest` with a `table_name` reflects that table via SQLAlchemy
  (`backend/app/ingestion/db_source.py`, no raw SQL string interpolation of the table name) and
  pulls its rows in.

Either kind, when the file/table name matches one of the reference dataset's own 8 files
(`backend/app/ingestion/hackathon_dataset.py`'s `KNOWN_TABLE_KEYS`), routes through that module's
canonical Asset-telemetry/Customer mapping instead of the generic
asset_id/metric/event_time/value/unit shape — the same mapping the standalone
`ingest_portfolio_series()`/`ingest_customers()` script already used, now reachable from the UI
too. `battery`/`scenario_actions` are recognized but still explicitly unmapped (reported as
`status: "not_mapped"`, not silently ingesting nothing) — same documented gap as the standalone
script. See `PRODUCTION_READINESS_REVIEW.md` §4 item 2/8.

## Portfolio-wide start/stop gate

Requested behaviour: the dashboard should not show live cards/decisions until an operator
explicitly starts analysis, and starting should require an actual connected data source first.
Implemented as `Tenant.operating_state` (`idle`|`running`, default `idle`) — `policy/cycle.py`
skips the decision cycle entirely for an idle tenant (the same early-return already used for "no
portfolio seeded yet"), and `GET/POST /operations/{status,start,stop}` exposes it. `start` 400s
unless at least one connector is `active` or at least one document has been ingested
(`has_data_source` in the status response). A successful ingestion in either Document Intake path
(`/ingestion/files` or `/ingestion/documents`) also auto-starts the tenant if it was idle — see
`mark_started_if_idle()` in `backend/app/routers/operations.py` — so uploading a dataset doesn't
require a separate manual Start click, matching the literal request that ingesting a document
should "initiate analysis and operation." The edge-simulator keeps publishing synthetic telemetry
regardless of this gate (a utility's sensor layer runs independently of whether anyone's currently
making decisions on top of it) — the gate controls the *decision cycle and dashboard display*,
not the underlying telemetry stream.

## Known tracked item: agent observability/eval is real but demo-scale

`AgentCallLog` persists every actual model-gateway call (real token usage, latency, schema-validity,
retries) — this is genuine telemetry, not synthetic. The evaluation harness
(`agent/eval_harness.py`) is 6 fixed cases across 3 agents, correctly distinguishing
schema-structural checks (pass under mock) from reasoning-behavioral checks (skipped under mock,
since mock cannot reason about a scenario) — but it is a demonstration of the *pattern*, not a
comprehensive eval program. A real one needs a much larger labelled corpus, ideally drawn from real
incident/decision history once the platform has some, plus statistical treatment over repeated runs
and human/rubric scoring for qualitative findings. There is also no timeout/circuit-breaker around
`ModelGateway.complete_structured` — a slow or unreachable Anthropic API currently just makes a
decision cycle run long rather than degrading gracefully.

## Known tracked item: frontend dependency advisories

`npm audit` flags moderate/high advisories in `vite`/`esbuild` (dev-server-only request forwarding,
not present in the production static build) and `react-router-dom` (an open-redirect variant and an
SSR-hydration deserialisation issue — this app has no SSR and builds no redirect target from user
input, so neither is reachable here). Both fixes require a major version bump (Vite 5→8, React
Router 6→7) outside the currently-declared semver ranges; `npm audit fix --force` applies them but
wasn't run this session so the bump could be verified properly (full re-test of routing and the dev
build) rather than shipped untested. Tracked here rather than silently ignored — bump both in a
dedicated follow-up pass.

## Object storage: SeaweedFS, not MinIO

The local/demo stack's S3-compatible object store was MinIO until MinIO discontinued free
distribution of its Docker images entirely — `minio/minio` and `minio/mc` on Docker Hub now return
"repository does not exist" (access denied, not a 404 on a specific tag), and even their
historically-free `dl.min.io` binary download server returns 410 Gone. This isn't a version-pin
problem to route around; it's a full shutdown of MinIO's free distribution channel.

Replaced with SeaweedFS's S3 gateway (`chrislusf/seaweedfs:4.22`, still freely pullable —
verified), which is a genuine drop-in here: every S3 call the app code makes (`quarantine.py`,
`routers/exports.py`, `export-worker/worker.py`) is a plain boto3 call —
`put_object`/`get_object`/`generate_presigned_url` — nothing MinIO-specific (no admin API, no
MinIO SDK). Verified against the actual running app, not just in isolation: triggered a real
evidence-pack export end-to-end (`export-worker` uploads via `put_object`, `GET /exports/{id}`
returns a presigned URL, downloaded the file over plain HTTP) and confirmed unauthenticated
requests are still correctly rejected (403).

What's different: bucket creation moved from `mc mb` (`minio-init`) to a small boto3 script
(`infrastructure/seaweedfs-init/bootstrap_buckets.py`, run as `seaweedfs-init` reusing the `api`
image the same way `migrate` reuses it for Alembic) that's idempotent the same way — catches
`BucketAlreadyOwnedByYou`/`BucketAlreadyExists` rather than erroring on a re-run. Credentials live
in a mounted `s3.json` identity file (`infrastructure/seaweedfs-init/s3.json`) instead of
`MINIO_ROOT_USER`/`PASSWORD` env vars, but the actual key/secret values (`S3_ACCESS_KEY`/
`S3_SECRET_KEY`) are unchanged, so no `.env` migration is needed. MinIO's bucket-oriented web
console (port 9001) has no equivalent here — SeaweedFS's filer serves a plain hierarchical
file-tree browser on the same port instead, which is what `docs/DEPLOYMENT.md` now links to.

## What is *not* simplified

Built with real logic end to end, calling live services where configured: ingestion (file/DB/
REST/streaming/multimodal PDF+image via vision), the digital twin, forecasting, the deterministic MILP optimizer + independent
feasibility validator, all specialist agents calling Anthropic Claude through a schema-validated
model gateway, the policy/safety engine's four autonomy modes, the OT gateway's independent
pre-dispatch revalidation, the append-only hash-chained decision/audit ledger, approvals, Connector
Studio (with real SSRF-hardened outbound HTTP to whatever endpoint a tenant admin configures),
Excel export, and multi-tenant isolation (enforced at the ORM layer, not just by convention).
