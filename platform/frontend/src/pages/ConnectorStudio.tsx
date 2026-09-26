import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface Connector {
  id: string; name: string; kind: string; endpoint_url: string; method: string; status: string;
  auth_type: string | null; credential_masked: Record<string, string> | null; schema_mapping: Record<string, string>;
  created_by: string | null; activated_by: string | null; last_test_result: any; created_at: string;
}

const KIND_OPTIONS = [
  { value: "generic", label: "Generic API", help: "Any external REST endpoint — an ERP/CRM webhook, a notification service, a custom integration." },
  { value: "market_energy_purchase", label: "Market energy purchase API", help: "A power exchange/aggregator's price or trading API, for agents to act on. Registers and reachability-tests the endpoint — it is not yet wired to replace the optimizer's synthetic price forecast (see ARCHITECTURE.md)." },
  { value: "scada", label: "SCADA API", help: "An external SCADA/OPC-UA gateway. Registers and reachability-tests the endpoint — live OT dispatch always goes through the separate, safety-critical ot-gateway-sim path, never a registered connector, regardless of activation status." },
  { value: "iot", label: "IoT API", help: "A device/sensor platform (smart meters, edge gateways). Registers and reachability-tests the endpoint — not yet wired to replace the edge-simulator's synthetic telemetry." },
  { value: "database", label: "Database", help: "A Postgres connection string (postgresql://user:pass@host:port/db) — e.g. the bundled reference source-db: postgresql://reo_source:reo-source-secret@source-db:5432/client_export. Once active, set a table name and \"Ingest now\" connects and pulls that table in, through the same reference-dataset mapping data_table ingestion uses below." },
  { value: "data_table", label: "Data table (path/link)", help: "A CSV/JSON/XLSX URL, or a filename from the platform's watched data folder (e.g. 03_renewable_generation.csv). A recognized reference-dataset filename is mapped onto real telemetry/customers automatically; anything else is expected in the generic asset_id/metric/event_time/value/unit shape." },
];

// Matches backend/app/routers/operations.py's has_data_source check exactly:
// only an active database or data_table connector counts — the other four
// kinds are for agents to act *out* on the world once a decision is made,
// not for bringing data in, so they deliberately don't gate Start Optimizer
// (by explicit request), and neither does a Document Intake upload on its
// own — see operations.py's module docstring.
const DATA_INGESTION_KINDS = ["database", "data_table"];

export default function ConnectorStudio() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("generic");
  const [url, setUrl] = useState("https://");
  const [apiKey, setApiKey] = useState("");
  const [tableName, setTableName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ingestMessage, setIngestMessage] = useState<string | null>(null);

  const { data, isLoading } = useQuery<Connector[]>({
    queryKey: ["connectors"],
    queryFn: async () => (await api.get("/connectors")).data,
    refetchInterval: 15000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["connectors"] });
    qc.invalidateQueries({ queryKey: ["operations-status"] });
  };

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/connectors", {
          name, kind, endpoint_url: url, method: "GET",
          auth_type: apiKey ? "api_key" : "none",
          credential_payload: apiKey ? { api_key: apiKey } : undefined,
          schema_mapping: kind === "database" && tableName ? { table_name: tableName } : {},
        })
      ).data,
    onSuccess: () => {
      invalidate();
      setShowForm(false);
      setName("");
      setKind("generic");
      setUrl("https://");
      setApiKey("");
      setTableName("");
      setError(null);
    },
    onError: (err: any) => setError(err?.response?.data?.detail || "Failed to create connector"),
  });

  const test = useMutation({ mutationFn: async (id: string) => (await api.post(`/connectors/${id}/test`)).data, onSuccess: invalidate });
  const activate = useMutation({
    mutationFn: async (id: string) => (await api.post(`/connectors/${id}/activate`)).data,
    onSuccess: invalidate,
    onError: (err: any) => setError(err?.response?.data?.detail || "Activation failed"),
  });
  const disable = useMutation({ mutationFn: async (id: string) => (await api.post(`/connectors/${id}/disable`)).data, onSuccess: invalidate });
  const ingest = useMutation({
    mutationFn: async (id: string) => (await api.post(`/connectors/${id}/ingest`)).data,
    onSuccess: (result) => {
      const detail = result.detail;
      const message = detail && detail.status !== "ingested"
        ? (detail.message || `${detail.table}: ${detail.status}`)
        : `${result.rows_queued} row(s)/reading(s) processed${detail?.table ? ` for ${detail.table}` : ""}.`;
      setIngestMessage(message);
      invalidate();
    },
    onError: (err: any) => setError(err?.response?.data?.detail || "Ingest failed"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  const isDataTable = kind === "data_table";
  const isDatabase = kind === "database";

  return (
    <div>
      <div className="row-between">
        <h2 style={{ fontSize: 15 }}>Connector Studio</h2>
        <button onClick={() => setShowForm((v) => !v)}>{showForm ? "Cancel" : "+ New connector"}</button>
      </div>
      <p className="muted">
        Register the APIs agents use to act on the outside world — a market energy purchase API, a SCADA API, an
        IoT device API — or a data table path/link for the platform to ingest as telemetry. Credentials go
        straight to the secrets vault and are never shown again. Activation requires a different user than the
        one who created it (maker-checker).
      </p>
      {error && <div className="error-banner">{error}</div>}
      {ingestMessage && <div className="evidence-box"><div className="finding">{ingestMessage}</div></div>}
      {showForm && (
        <form onSubmit={onSubmit} className="card" style={{ marginBottom: 16 }}>
          <div className="field">
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required style={{ width: "100%" }} />
          </div>
          <div className="field">
            <label>Kind</label>
            <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: "100%" }}>
              {KIND_OPTIONS.map((k) => (
                <option key={k.value} value={k.value}>{k.label}</option>
              ))}
            </select>
            <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
              {KIND_OPTIONS.find((k) => k.value === kind)?.help}
            </p>
          </div>
          <div className="field">
            <label>
              {isDataTable ? "Data path / link (.csv/.json/.xlsx URL, or a watched-folder filename)"
                : isDatabase ? "Connection string (postgresql://...)" : "Endpoint URL"}
            </label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              placeholder={
                isDataTable ? "https://example.com/exports/telemetry.csv  or  03_renewable_generation.csv"
                  : isDatabase ? "postgresql://reo_source:reo-source-secret@source-db:5432/client_export" : undefined
              }
              style={{ width: "100%" }}
            />
          </div>
          {isDatabase && (
            <div className="field">
              <label>Table name</label>
              <input
                value={tableName}
                onChange={(e) => setTableName(e.target.value)}
                placeholder="renewable_generation"
                style={{ width: "100%" }}
              />
              <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                Reference tables available on source-db: customer_demographics, customer_energy_consumption_tariff,
                renewable_generation, grid, market, external_weather.
              </p>
            </div>
          )}
          <div className="field">
            <label>API key (optional — stored encrypted, never shown again)</label>
            <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" style={{ width: "100%" }} />
          </div>
          <button type="submit" disabled={create.isPending}>Create (draft)</button>
        </form>
      )}
      {isLoading && <div className="empty-state">Loading...</div>}
      {data && (
        <div className="table-scroll">
        <table>
          <thead><tr><th>Name</th><th>Kind</th><th>Endpoint</th><th>Status</th><th>Credential</th><th>Last test</th><th></th></tr></thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td><Badge text={c.kind} /></td>
                <td className="mono" style={{ whiteSpace: "nowrap" }}>
                  {c.endpoint_url}
                  {c.kind === "database" && c.schema_mapping?.table_name && (
                    <div className="muted" style={{ fontSize: 11 }}>table: {c.schema_mapping.table_name}</div>
                  )}
                </td>
                <td><Badge text={c.status} /></td>
                <td className="muted">{c.credential_masked ? JSON.stringify(c.credential_masked) : "none"}</td>
                <td>{c.last_test_result ? (c.last_test_result.http_status ?? (c.last_test_result.ssrf_allowed ? "reachable?" : "blocked")) : "—"}</td>
                <td className="row">
                  <button className="secondary" onClick={() => test.mutate(c.id)} disabled={test.isPending}>Test</button>
                  {c.status !== "active" && c.status !== "disabled" && (
                    <button onClick={() => activate.mutate(c.id)} disabled={activate.isPending}>Activate</button>
                  )}
                  {(c.kind === "data_table" || c.kind === "database") && c.status === "active" && (
                    <button onClick={() => ingest.mutate(c.id)} disabled={ingest.isPending}>
                      {ingest.isPending ? "Ingesting..." : "Ingest now"}
                    </button>
                  )}
                  {c.status !== "disabled" && (
                    <button className="danger" onClick={() => disable.mutate(c.id)} disabled={disable.isPending}>Disable</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {data && !data.some((c) => c.status === "active" && DATA_INGESTION_KINDS.includes(c.kind)) && (
        <p className="muted" style={{ fontSize: 11, marginTop: 10 }}>
          No active database or data_table connector yet — Portfolio Operations' Start Optimizer needs one of those
          two kinds active before it can begin (a Document Intake upload alone no longer unlocks it).
        </p>
      )}
    </div>
  );
}
