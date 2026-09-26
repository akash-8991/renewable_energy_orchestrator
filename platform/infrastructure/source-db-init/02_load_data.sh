#!/bin/bash
# Runs after 01_create_tables.sql (docker-entrypoint-initdb.d executes
# scripts in this directory in filename order). /csv is the reference
# dataset mounted read-only from ../../data — but that directory is
# gitignored (a user-provided, ~100MB dataset, never committed), so on a
# fresh clone or in CI it's empty. A .sh init script (unlike a plain .sql
# one) lets that be a graceful skip instead of a fatal `\copy` error that
# would otherwise abort this container's entire initialization. No `exit`/
# `return` here: the postgres entrypoint runs this file directly if it's
# executable, but *sources* it in the current shell if not — an `exit`
# would kill the parent entrypoint process in that case, not just this
# script, so control simply falls off the end of the if/else instead.
set -euo pipefail

if [ -f /csv/01_customer_demographics.csv ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
	\copy customer_demographics FROM '/csv/01_customer_demographics.csv' WITH (FORMAT csv, HEADER true)
	\copy customer_energy_consumption_tariff FROM '/csv/02_customer_energy_consumption_tariff.csv' WITH (FORMAT csv, HEADER true)
	\copy renewable_generation FROM '/csv/03_renewable_generation.csv' WITH (FORMAT csv, HEADER true)
	\copy grid FROM '/csv/04_grid.csv' WITH (FORMAT csv, HEADER true)
	\copy battery FROM '/csv/05_battery.csv' WITH (FORMAT csv, HEADER true)
	\copy market FROM '/csv/06_market.csv' WITH (FORMAT csv, HEADER true)
	\copy external_weather FROM '/csv/07_external_weather.csv' WITH (FORMAT csv, HEADER true)
	\copy scenario_actions FROM '/csv/08_scenario_actions.csv' WITH (FORMAT csv, HEADER true)
	EOSQL
else
  echo "source-db-init: reference dataset not found under /csv (platform's ../data/ is empty or missing — it's gitignored, user-provided). Tables created empty; drop the 8 reference CSVs into platform/../data/ and recreate this container's volume (docker compose down -v && up -d source-db) to populate them."
fi
