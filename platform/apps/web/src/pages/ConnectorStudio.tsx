import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface Connector {
  id: string; name: string; kind: string; endpoint_url: string; method: string; status: string;
  auth_type: string | null; credential_masked: Record<string, string> | null;
  created_by: string | null; activated_by: string | null; last_test_result: any; created_at: string;
}

const KIND_OPTIONS = [
  { value: "generic", label: "Generic API", help: "Any external REST endpoint — an ERP/CRM webhook, a notification service, a custom integration." },
  { value: "market_data", label: "Market data / energy procurement", help: "A power exchange or aggregator's price/trading API. Registers and reachability-tests the endpoint — it is not yet wired to replace the optimizer's synthetic price forecast (see ARCHITECTURE.md)." },
  { value: "database", label: "Database", help: "A database reachable over HTTP (e.g. a PostgREST/REST facade). This is still an HTTP endpoint under the hood — a raw DB driver connection string isn't supported here." },
  { value: "scada_bridge", label: "SCADA / OT bridge", help: "An external SCADA/OPC-UA gateway. Registers and reachability-tests the endpoint — live OT dispatch still goes through the separate, safety-critical ot-gateway-sim path, not this connector." },
];

export default function ConnectorStudio() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("generic");
  const [url, setUrl] = useState("https://");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery<Connector[]>({
    queryKey: ["connectors"],
    queryFn: async () => (await api.get("/connectors")).data,
    refetchInterval: 15000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["connectors"] });

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/connectors", {
          name, kind, endpoint_url: url, method: "GET",
          auth_type: apiKey ? "api_key" : "none",
          credential_payload: apiKey ? { api_key: apiKey } : undefined,
        })
      ).data,
    onSuccess: () => {
      invalidate();
      setShowForm(false);
      setName("");
      setKind("generic");
      setUrl("https://");
      setApiKey("");
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

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <div>
      <div className="row-between">
        <h2 style={{ fontSize: 15 }}>Connector Studio</h2>
        <button onClick={() => setShowForm((v) => !v)}>{showForm ? "Cancel" : "+ New connector"}</button>
      </div>
      <p className="muted">
        Register a client API endpoint and authentication. Credentials go straight to the secrets vault and are never
        shown again. Activation requires a different user than the one who created it (maker-checker).
      </p>
      {error && <div className="error-banner">{error}</div>}
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
            <label>Endpoint URL</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} required style={{ width: "100%" }} />
          </div>
          <div className="field">
            <label>API key (optional — stored encrypted, never shown again)</label>
            <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" style={{ width: "100%" }} />
          </div>
          <button type="submit" disabled={create.isPending}>Create (draft)</button>
        </form>
      )}
      {isLoading && <div className="empty-state">Loading...</div>}
      {data && (
        <table>
          <thead><tr><th>Name</th><th>Kind</th><th>Endpoint</th><th>Status</th><th>Credential</th><th>Last test</th><th></th></tr></thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td><Badge text={c.kind} /></td>
                <td className="mono" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>{c.endpoint_url}</td>
                <td><Badge text={c.status} /></td>
                <td className="muted">{c.credential_masked ? JSON.stringify(c.credential_masked) : "none"}</td>
                <td>{c.last_test_result ? (c.last_test_result.http_status ?? (c.last_test_result.ssrf_allowed ? "reachable?" : "blocked")) : "—"}</td>
                <td className="row">
                  <button className="secondary" onClick={() => test.mutate(c.id)} disabled={test.isPending}>Test</button>
                  {c.status !== "active" && c.status !== "disabled" && (
                    <button onClick={() => activate.mutate(c.id)} disabled={activate.isPending}>Activate</button>
                  )}
                  {c.status !== "disabled" && (
                    <button className="danger" onClick={() => disable.mutate(c.id)} disabled={disable.isPending}>Disable</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
