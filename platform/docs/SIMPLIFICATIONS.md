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
- **`.docx` is read as text, not rendered to an image.** There's no natural "page image" for a
  Word doc the way there is for a scan/photo, so `extract_text()` pulls its paragraphs and table
  cells (in document order, via `python-docx`) and hands that to the same agent/schema as a
  text-only model call instead of a vision one. Legacy `.doc` (pre-2007 binary Word) is not
  supported — it needs a much heavier dependency (LibreOffice headless conversion) that wasn't
  added since no real source files arrived in that format.

## Known tracked item: generic tabular-file mapping (arbitrary csv/json/xlsx structure)

The reference dataset's 8 files and the fixed asset_id/metric/event_time/value/unit shape cover
known structures; a genuinely arbitrary file (a client's own SCADA export, a spreadsheet with
different column names) previously just 400'd with "no valid rows found". `POST /ingestion/files`
now falls through to a mapping agent (`backend/app/ingestion/generic_table_mapper.py`) as a last
resort: it's shown the file's column headers, a few sample rows, and the tenant's real asset
list, and proposes a role for each column (a timestamp, an asset/customer reference, or a named
metric/field) — never applied on its own say-so. A human reviews the proposal in Document Intake
(`GET /ingestion/mapping-proposals`, `POST .../{id}/{approve,reject}`) before anything is
actually ingested, consistent with the platform's non-negotiable "agents produce evidence, never
commands" rule.

**The actual token-reduction mechanism, and why it isn't a semantic cache.** Once a human approves
a mapping for a given column layout (a hash of the sorted, lowercased column names —
`column_signature()`), that decision is stored as a `TableMappingRule` and reused deterministically
for every future upload with the identical layout — verified live: a second file with the same 4
columns as an approved one ingested immediately with `mapping_rule_reused: true` in the response
and no further model call. This is exact-match reuse of a human-confirmed decision, not a
similarity/embedding cache over merely-similar files — deliberately, since guessing that a *new*
file is "close enough" to reuse a past mapping for is exactly the kind of silent misapplication
the platform's evidence-not-commands philosophy exists to prevent. `times_reused` on each rule
makes the established patterns directly visible (queryable via the table, not yet surfaced in a
dedicated UI), which is the "patterns for decisions are clearly identifiable" property this was
built for.

Known limits, stated rather than hidden:
- **Verified with a hand-constructed mapping, not a real model call**, for the same reason as
  D3 above — the configured OpenRouter key ran out of credits mid-session (a real, live 402 from
  the provider, which the existing `GatewayError` fail-closed handling caught and surfaced
  correctly). `apply_mapping()`'s actual data transformation (asset-name resolution, wide-to-long
  reshaping, customer-field updates, the full approve→ingest→cache-reuse round trip through the
  real API) was verified end-to-end against the live stack; the *quality* of the agent's own
  column-role judgment was only verified via the mock gateway's schema-shape check, not a real
  reasoning pass.
- **Telemetry mapping requires an explicit per-row asset reference column.** A portfolio-wide file
  with no per-asset breakdown (like the reference dataset's own grid/market/weather files) isn't
  this agent's job — that's what the reference-dataset path (`hackathon_dataset.py`) already
  handles with its own capacity-weighted distribution logic. The mapping agent's system prompt
  explicitly tells it to report `unrecognized` rather than invent a distribution when there's no
  asset column.
- **Asset/customer reference resolution is exact, case-insensitive string matching only** — no
  fuzzy matching. A row whose reference doesn't match a real asset/customer name exactly is
  skipped and counted (`skipped_unmatched_asset_ref`/`skipped_unknown_customer`), never guessed.

## Known tracked item: Connector Studio — five of six kinds are genuine data/action paths, `scada` never will be

`GET/POST /connectors` (SSRF-hardened, vaulted credentials, maker-checker) categorises a
connector's `kind` (`generic`/`market_energy_purchase`/`scada`/`iot`/`database`/`data_table`).
Originally four of the six were registration-only (store config, run a reachability test, gate
activation, nothing more); a same-day follow-up closed two of those four:

- `market_energy_purchase`: `POST /connectors/{id}/ingest` (an active connector only) fetches its
  `endpoint_url`, expects a JSON array (or `{"prices": [...]}`) of `{timestamp, price_per_mwh}`
  objects (`parse_market_price_entries()` in `backend/app/routers/connectors.py`, pure and unit-
  tested independent of any DB/HTTP/FastAPI context), and writes it as a real `Forecast` series
  (`variable="price"`, `model_version="live-market-v1"`) for every `grid_interconnection` asset —
  taking over from the synthetic price curve for whatever it covers.
- `iot`: `POST /connectors/{id}/ingest` fetches its `endpoint_url` and parses the response through
  the identical generic asset_id/metric/event_time/value/unit shape a file upload or `data_table`
  connector already uses, publishing it as real telemetry via the same `publish_readings()` path.

Neither of these two kinds gates or auto-starts the optimizer, by the same explicit request that
narrowed the gate to `database`/`data_table` only (see "Portfolio-wide start/stop gate" below) —
they're real ingestion, just not the two kinds treated as "a connected data source."

`generic` remains a plain registration/reachability-test surface (there is no fixed shape to ingest
against — it's whatever a bespoke external system happens to expose), and **`scada` specifically
never will ingest or dispatch through this surface**: doc 05's non-negotiable architecture keeps OT
command dispatch on the independent, safety-critical `ot-gateway-sim` path exclusively, so a
user-registered SCADA connector deliberately has no route into real command execution regardless
of its activation status, by design, not by omission — this is the one item on
`PRODUCTION_READINESS_REVIEW.md`'s punch list that isn't closed by more engineering effort, ever.

`data_table` and `database` were already genuine, working data paths before this pass:

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

Any of these four ingesting kinds, when the file/table name matches one of the reference dataset's own 8 files
(`backend/app/ingestion/hackathon_dataset.py`'s `KNOWN_TABLE_KEYS`), routes through that module's
canonical Asset-telemetry/Customer mapping instead of the generic
asset_id/metric/event_time/value/unit shape — the same mapping the standalone
`ingest_portfolio_series()`/`ingest_customers()` script already used, now reachable from the UI
too. Document Intake's own file upload (`POST /ingestion/files`) recognizes the same 8 filenames
the same way, so dropping e.g. `01_customer_demographics.csv` there works too, not just through a
connector — previously it 400'd with "no valid rows found", since it only ever knew the generic
shape (also raised that endpoint's size cap from 25MB to 100MB, since
`02_customer_energy_consumption_tariff.csv` alone is 76MB). `battery`/`scenario_actions` are
recognized but still explicitly unmapped (reported as
`status: "not_mapped"`, not silently ingesting nothing) — same documented gap as the standalone
script.

## Portfolio-wide start/stop gate

Requested behaviour: the dashboard should not show live cards/decisions until an operator
explicitly starts analysis, and starting should require an actual connected data source first.
Implemented as `Tenant.operating_state` (`idle`|`running`, default `idle`) — `policy/cycle.py`
skips the decision cycle entirely for an idle tenant (the same early-return already used for "no
portfolio seeded yet"), and `GET/POST /operations/{status,start,stop}` exposes it. `start` 400s
unless at least one connector of kind `database` or `data_table` is `active`
(`DATA_INGESTION_CONNECTOR_KINDS` in `backend/app/routers/operations.py`; `has_data_source` in the
status response). By explicit request, only these two connector kinds gate/auto-start the
optimizer — the other four registrable kinds (`generic`, `market_energy_purchase`, `scada`, `iot`)
are for agents to act *out* on the world once a decision is made (not yet wired to real action
execution — see `ARCHITECTURE.md`), not for bringing data in, so an active one of those does not
count. Document Intake uploads (`/ingestion/files`, `/ingestion/documents`) also deliberately do
*not* auto-start the tenant — they remain a real, independent ingestion path (see the "Connector
Studio is a registration surface for four of six kinds" item above for the `data_table`/`database`
ingestion mechanics they share), but starting analysis is reserved for an explicit Connector
Studio connection. `document_count`/`active_connector_count` are still reported in the status
response for visibility, but only the latter (restricted to the two data-ingestion kinds) drives
`has_data_source`. The edge-simulator keeps publishing synthetic telemetry regardless of this gate
(a utility's sensor layer runs independently of whether anyone's currently making decisions on top
of it) — the gate controls the *decision cycle and dashboard display*, not the underlying
telemetry stream.

## Known tracked item: agent observability/eval is real but demo-scale

`AgentCallLog` persists every actual model-gateway call (real token usage, latency, schema-validity,
retries) — this is genuine telemetry, not synthetic. The evaluation harness
(`evaluation/eval_harness.py`) grew, in a same-day follow-up, from 6 fixed cases across 3 agents to
**15 cases across 7 of the 9 specialist agents** (data_quality, risk_critic, governance, forecast,
asset, market — structural only, see the harness's own module docstring for why — and grid,
optimisation_reviewer), each behavioral case grounded in a documented, unambiguous rule from that
agent's own system prompt (e.g. "a solar asset with zero forecast rows is at least a MEDIUM
finding") rather than a guessed threshold. It correctly distinguishes schema-structural checks (pass
under mock) from reasoning-behavioral checks (skipped under mock, since mock cannot reason about a
scenario, verified by `tests/test_eval_harness.py`) — but it is still, honestly, a demonstration of
the *pattern* at a larger scale, not a comprehensive eval program. **Still open, and not a coding
task**: a real one needs a much larger labelled corpus, ideally drawn from real incident/decision
history once the platform has some, plus statistical treatment over repeated runs and human/rubric
scoring for qualitative findings — none of which a coding session can manufacture without real
usage data.

A related gap — "no timeout/circuit-breaker around `ModelGateway.complete_structured`" — was closed
the same day; see "Configuration Studio and the same-day production-readiness closures" below.

## Known tracked item: frontend dependency advisories — closed

`npm audit` used to flag moderate/high advisories in `vite`/`esbuild` (dev-server-only request
forwarding, not present in the production static build) and `react-router-dom` (an open-redirect
variant and an SSR-hydration deserialisation issue — this app has no SSR and builds no redirect
target from user input, so neither was reachable here even before the fix). Both required a major
version bump outside the previously-declared semver ranges (`vite` 5→8, `react-router-dom` 6→7,
`@vitejs/plugin-react` 4→6 for the matching peer range) — done in a same-day follow-up via `npm
audit fix --force`, verified with a full regression pass rather than shipped untested: a clean `tsc
-b && vite build`, and a live browser walkthrough (password login, SSO login via the new Keycloak
integration, Portfolio Operations, Connector Studio, Configuration Studio) with no runtime errors.
`npm audit` now reports 0 vulnerabilities. This codebase only uses react-router's classic
declarative API (`BrowserRouter`/`Routes`/`Route`/`Link`/`NavLink`/`useNavigate`/`useLocation`),
which v7 keeps backward-compatible — the data-router/loader APIs that would need real migration work
were never used here.

## Configuration Studio and the same-day production-readiness closures

`PRODUCTION_READINESS_REVIEW.md`'s consolidated punch list (§4) named eight follow-up items. AWS
apply, real SCADA/OT hardware, legal/compliance sign-off, and production secrets management are
explicitly not coding tasks (no real cloud account, site survey, DPO, or KMS setup exists for a
coding session to use) and are unchanged. The four that *are* coding tasks were closed the same day,
made runtime-configurable rather than env-var-only, and surfaced in a new **Configuration Studio**
dashboard page (`manage:settings`/`manage:platform_config`, backed by a new tenant-scoped
`PlatformSettings` table, `backend/app/routers/configuration.py`):

- **Live weather feed** (`policy/live_weather.py`): a real, keyless call to Open-Meteo's forecast
  API for a configured site lat/lon, replacing the synthetic solar diurnal curve / seasonal-naive
  wind speed proxy in `policy/forecast.py` with real forecasted cloud-cover/wind-speed data for the
  whole 24h horizon. Falls back to the synthetic model automatically (disabled, no coordinates, or
  the API call fails for any reason) — verified against the real Open-Meteo endpoint, not mocked.
- **Model gateway circuit breaker** (`guardrails/circuit_breaker.py`): a per-tenant, Redis-backed
  breaker + call timeout wrapped around `ModelGateway.complete_structured` — opens after N
  consecutive failures (schema-invalid, timeout, provider error), so a slow/down provider degrades a
  decision cycle gracefully (immediate fail-closed, zero tokens spent) instead of just running long.
  Thresholds are per-tenant and live-editable from Configuration Studio.
- **Real external IdP** (`backend/app/routers/auth.py`'s `/auth/sso/{login,callback}` +
  `infrastructure/keycloak-init/realm-export.json`): a genuine OIDC authorization-code flow against
  a bundled, real, spec-compliant Keycloak container — actual browser redirect, server-to-server
  token exchange, and a JWKS-verified `id_token` (tested against both a correctly-signed and a
  forged token, so the signature check is proven to actually reject a bad token, not just accept a
  good one). Auto-provisions a local `User` row (linking by email if one already exists) on first
  SSO login. Per-tenant opt-in (`PlatformSettings.sso_enabled`) since not every tenant necessarily
  federates with the same IdP. Verified live end-to-end via the actual browser flow, not just unit
  tests. Its login page is reskinned to match the dashboard's own login screen
  (`infrastructure/keycloak-theme/reo` — a CSS-only Keycloak theme override: same brand gradient,
  card styling, button/input treatment and font stack, none of Keycloak's actual login logic
  touched) so the SSO redirect doesn't feel like a jump to a visually unrelated product.
- **`market_energy_purchase`/`iot` connector ingestion** — see the Connector Studio section above.

This is deliberately still bounded by the same honesty standard as the rest of this document: the
live weather feed is real data from a real (if free-tier) provider, not a second synthetic model in
disguise; the Keycloak integration is a real, standard IdP, not a stub that always says "yes"; and
none of this claims to close the AWS/hardware/legal/secrets items, which remain exactly as described
in `PRODUCTION_READINESS_REVIEW.md`.

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
