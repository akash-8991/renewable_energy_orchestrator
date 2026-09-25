# Reference dataset ingestion

The reference dataset (`Renewable_Energy_Orchestrator_Dataset` — 100 UK customers, 15-minute
resolution, 2026-01-01 to 2026-03-31, reproducible seed 42) lives in `../data/` (gitignored — 100+MB
of CSVs don't belong in git history; mounted read-only into the `api` container by
`infrastructure/docker-compose.yml` as `/data`, same mount the folder-watcher already used).

## What's ingested, and how

`backend/app/ingestion/hackathon_dataset.py` — `python -m app.ingestion.hackathon_dataset` (or
`ingest_portfolio_series()` directly) — ingests the four **portfolio-level** files as real
telemetry readings, through the exact same `reo.telemetry` Redis stream + validated consumer path
every other reading (live edge-simulator, uploaded CSV, folder-watched file) goes through:

| Dataset file | Column | Platform metric | Attributed to |
|---|---|---|---|
| `03_renewable_generation.csv` | `solar_output_mw` | `power_kw` | every solar asset, split by rated-capacity share |
| `03_renewable_generation.csv` | `wind_output_mw` | `power_kw` | every wind asset, split by rated-capacity share |
| `04_grid.csv` | `grid_frequency_hz` | `frequency_hz` | the grid interconnection asset |
| `06_market.csv` | `electricity_price_gbp_mwh` | `market_price_gbp_per_mwh` | the grid interconnection asset |
| `07_external_weather.csv` | `temperature_c` | `temperature_c` | every solar asset |
| `07_external_weather.csv` | `wind_speed_mps` | `wind_speed_ms` | every wind asset |

Every reading is tagged `source: hackathon_dataset:<file>` (queryable/traceable), and every metric
used is one the platform already models (`_KNOWN_METRICS` in `telemetry_consumer.py`) — no schema
changes needed. Timestamps are shifted so the most recent requested row lands at "now" (default:
last 8h · 32 rows), preserving each row's original 15-minute spacing — this replays real historical
values as current telemetry; it does not fabricate any value. Verified live: ingested readings are
visible via `GET /twin/portfolio` correctly split proportional to each asset's rated capacity (e.g.
Solway Solar Park A at 40 MW rated gets ~1.14× Solway B's 35 MW share of the aggregate solar
figure), and coexist normally with the ongoing live edge-simulator telemetry on the same table.

`08_scenario_actions.csv` (8,636 rows, expert-labelled `recommended_action_context`) contains
exactly one `storm_alert` row (`"High wind warning"`, 2026-03-13, labelled *"Reserve battery /
reduce exposure"*). Rather than force this into the agent evaluation harness — none of the
existing specialist agents' prompts are storm-alert-aware, and inventing a new evidence field only
that one eval case would read isn't a genuine regression check — it was used to verify the D3
multimodal document-intake path (`POST /ingestion/documents`, see `ARCHITECTURE.md`'s D3 section)
end-to-end against a **real** labelled scenario instead of a synthetic one: rendered as a weather-
alert notice image and run through the live endpoint with real OpenRouter vision. Extraction was
correct on every field, including recovering the exact original timestamp
(`2026-03-13T03:15:00+00:00`) purely from the image.

## What's deliberately not ingested

`01_customer_demographics.csv`, `02_customer_energy_consumption_tariff.csv` (79MB), and
`05_battery.csv` (22MB) describe **100 individual customers'** demographics, 15-minute
consumption/billing, and per-customer battery telemetry. The platform's canonical model
(`models/canonical.py`) has no "individually metered customer" entity — `Asset`/`Battery` model a
portfolio's generation/storage/demand *assets* (a handful of solar farms, wind farms, BESS units,
consumer load groups), not 100 separate domestic/SME meters. Force-mapping 100 customers onto the
existing consumer `Asset` rows (there are 6) would misrepresent what those rows are, and genuinely
modelling this dimension needs a new canonical entity (something like `Customer`/`Meter`, plus the
RBAC/privacy questions that come with per-customer data) — a real schema extension, not a same-
session ingestion script. Flagged here rather than silently skipped.
