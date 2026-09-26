-- Mirrors the reference dataset's 8 CSVs (../../data/*.csv) as plain SQL
-- tables — "a client's own database" for Connector Studio's `database` kind
-- to connect to. Column order matches each CSV's header exactly, since
-- 02_load_data.sh's \copy maps CSV columns to table columns positionally
-- (that file also handles ../../data/ being absent — it's gitignored,
-- user-provided — by creating these tables empty instead of failing).
-- No PRIMARY KEY/UNIQUE constraints: this is a bulk one-shot load on first
-- boot (see docker-entrypoint-initdb.d), and a constraint violation would
-- abort \copy partway through rather than degrade gracefully.

CREATE TABLE customer_demographics (
    customer_id TEXT,
    customer_type TEXT,
    region TEXT,
    annual_consumption_kwh NUMERIC,
    renewable_profile TEXT,
    solar_capacity_kw NUMERIC,
    wind_capacity_kw NUMERIC,
    battery_installed INTEGER,
    battery_capacity_kwh NUMERIC,
    tariff_plan TEXT,
    standing_charge_gbp_day NUMERIC,
    base_rate_gbp_kwh NUMERIC,
    offpeak_rate_gbp_kwh NUMERIC,
    occupants NUMERIC,
    property_size_m2 NUMERIC,
    business_size TEXT
);
CREATE INDEX ix_customer_demographics_customer_id ON customer_demographics (customer_id);

CREATE TABLE customer_energy_consumption_tariff (
    "timestamp" TIMESTAMPTZ,
    customer_id TEXT,
    consumption_kwh NUMERIC,
    solar_generation_kwh NUMERIC,
    wind_generation_kwh NUMERIC,
    net_grid_import_kwh NUMERIC,
    export_kwh NUMERIC,
    tariff_plan TEXT,
    energy_rate_gbp_kwh NUMERIC,
    standing_charge_gbp_15min NUMERIC
);
CREATE INDEX ix_cect_customer_time ON customer_energy_consumption_tariff (customer_id, "timestamp");

CREATE TABLE renewable_generation (
    "timestamp" TIMESTAMPTZ,
    solar_output_mw NUMERIC,
    solar_forecast_mw NUMERIC,
    wind_output_mw NUMERIC,
    wind_forecast_mw NUMERIC
);
CREATE INDEX ix_renewable_generation_time ON renewable_generation ("timestamp");

CREATE TABLE grid (
    "timestamp" TIMESTAMPTZ,
    current_demand_mw NUMERIC,
    demand_forecast_mw NUMERIC,
    grid_frequency_hz NUMERIC,
    transmission_constraint TEXT
);
CREATE INDEX ix_grid_time ON grid ("timestamp");

CREATE TABLE battery (
    "timestamp" TIMESTAMPTZ,
    customer_id TEXT,
    battery_capacity_kwh NUMERIC,
    state_of_charge_pct NUMERIC,
    charging_efficiency_pct NUMERIC,
    max_charge_rate_kw NUMERIC,
    max_discharge_rate_kw NUMERIC,
    battery_degradation_pct NUMERIC
);
CREATE INDEX ix_battery_customer_time ON battery (customer_id, "timestamp");

CREATE TABLE market (
    "timestamp" TIMESTAMPTZ,
    electricity_price_gbp_mwh NUMERIC,
    carbon_price_gbp_tco2e NUMERIC,
    demand_response_incentive_gbp_mwh NUMERIC
);
CREATE INDEX ix_market_time ON market ("timestamp");

CREATE TABLE external_weather (
    "timestamp" TIMESTAMPTZ,
    temperature_c NUMERIC,
    wind_speed_mps NUMERIC,
    cloud_cover_fraction NUMERIC,
    storm_alert TEXT,
    maintenance_schedule TEXT
);
CREATE INDEX ix_external_weather_time ON external_weather ("timestamp");

CREATE TABLE scenario_actions (
    "timestamp" TIMESTAMPTZ,
    electricity_price_gbp_mwh NUMERIC,
    solar_output_mw NUMERIC,
    wind_output_mw NUMERIC,
    current_demand_mw NUMERIC,
    storm_alert TEXT,
    maintenance_schedule TEXT,
    transmission_constraint TEXT,
    recommended_action_context TEXT
);
CREATE INDEX ix_scenario_actions_time ON scenario_actions ("timestamp");
