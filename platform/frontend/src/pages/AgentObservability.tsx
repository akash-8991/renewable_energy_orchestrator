import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface AgentSummaryRow {
  agent: string; total_calls: number; schema_valid_calls: number; retried_calls: number;
  avg_latency_ms: number; p95_latency_ms: number; total_input_tokens: number; total_output_tokens: number;
}

interface ObservabilitySummary {
  total_calls: number; schema_valid_rate: number; retry_rate: number;
  by_agent: AgentSummaryRow[]; providers_in_use: string[];
}

interface AgentCall {
  id: string; agent: string; correlation_id: string; provider: string; model: string; latency_ms: number;
  input_tokens: number | null; output_tokens: number | null; schema_valid: boolean; retried: boolean;
  error: string | null; created_at: string;
}

interface EvalRun {
  id: string; triggered_by: string | null; model_provider: string; total_cases: number; passed_cases: number;
  results: { case: string; agent: string; tier: string; outcome: string; detail: string }[]; created_at: string;
}

function CallMetricsTab() {
  const { data: summary, isLoading } = useQuery<ObservabilitySummary>({
    queryKey: ["observability-summary"],
    queryFn: async () => (await api.get("/observability/summary")).data,
    refetchInterval: 20000,
  });
  const { data: calls } = useQuery<AgentCall[]>({
    queryKey: ["agent-calls"],
    queryFn: async () => (await api.get("/observability/agent-calls", { params: { limit: 100 } })).data,
    refetchInterval: 20000,
  });

  if (isLoading) return <div className="empty-state">Loading...</div>;

  return (
    <div>
      <div className="grid grid-cards" style={{ marginBottom: 16 }}>
        <div className="card">
          <h3>Calls (24h)</h3>
          <div className="big">{summary?.total_calls ?? 0}</div>
        </div>
        <div className="card">
          <h3>Schema-valid rate</h3>
          <div className="big">{summary ? (summary.schema_valid_rate * 100).toFixed(1) : "—"}%</div>
        </div>
        <div className="card">
          <h3>Retry rate</h3>
          <div className="big">{summary ? (summary.retry_rate * 100).toFixed(1) : "—"}%</div>
        </div>
        <div className="card">
          <h3>Providers in use</h3>
          <div>{summary?.providers_in_use.length ? summary.providers_in_use.map((p) => <Badge key={p} text={p} />) : <span className="muted">—</span>}</div>
        </div>
      </div>

      <h3 style={{ fontSize: 13, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }}>Per-agent (24h)</h3>
      {summary && summary.by_agent.length > 0 ? (
        <div className="table-scroll" style={{ marginBottom: 20 }}>
        <table>
          <thead>
            <tr><th>Agent</th><th>Calls</th><th>Schema-valid</th><th>Retried</th><th>Avg latency</th><th>P95 latency</th><th>Tokens (in/out)</th></tr>
          </thead>
          <tbody>
            {summary.by_agent.map((a) => (
              <tr key={a.agent}>
                <td>{a.agent}</td>
                <td>{a.total_calls}</td>
                <td>{a.schema_valid_calls}/{a.total_calls}</td>
                <td>{a.retried_calls}</td>
                <td>{a.avg_latency_ms.toFixed(0)}ms</td>
                <td>{a.p95_latency_ms.toFixed(0)}ms</td>
                <td className="muted">{a.total_input_tokens} / {a.total_output_tokens}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : (
        <div className="muted" style={{ marginBottom: 20 }}>No agent calls in the last 24h.</div>
      )}

      <h3 style={{ fontSize: 13, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }}>Recent calls</h3>
      {calls && calls.length > 0 ? (
        <div className="table-scroll">
        <table>
          <thead>
            <tr><th>Time</th><th>Agent</th><th>Provider/Model</th><th>Latency</th><th>Schema</th><th>Retried</th><th>Error</th></tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr key={c.id}>
                <td className="mono" style={{ fontSize: 12 }}>{new Date(c.created_at).toLocaleString()}</td>
                <td>{c.agent}</td>
                <td className="muted">{c.provider}/{c.model}</td>
                <td>{c.latency_ms.toFixed(0)}ms</td>
                <td><Badge text={c.schema_valid ? "valid" : "invalid"} /></td>
                <td>{c.retried ? <Badge text="retried" /> : "—"}</td>
                <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{c.error || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : (
        <div className="empty-state">No calls recorded yet.</div>
      )}
    </div>
  );
}

function EvaluationTab() {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: runs, isLoading } = useQuery<EvalRun[]>({
    queryKey: ["eval-runs"],
    queryFn: async () => (await api.get("/observability/eval-runs")).data,
    refetchInterval: 10000,
  });

  // A run takes minutes (one live model call per case) and its result only exists when it finishes,
  // so track "is one queued or running" and refresh the list the moment it ends.
  const { data: status } = useQuery<{ running: boolean; started_at: string | null }>({
    queryKey: ["eval-status"],
    queryFn: async () => (await api.get("/observability/eval-runs/status")).data,
    refetchInterval: (query) => (query.state.data?.running ? 3000 : 15000),
  });
  const running = !!status?.running;
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) qc.invalidateQueries({ queryKey: ["eval-runs"] });
    wasRunning.current = running;
  }, [running, qc]);

  const [downloading, setDownloading] = useState<string | null>(null);
  async function downloadRun(run: EvalRun) {
    setDownloading(run.id);
    try {
      const resp = await api.get(`/observability/eval-runs/${run.id}/export`, { responseType: "blob" });
      const url = URL.createObjectURL(resp.data);
      const link = document.createElement("a");
      const stamp = new Date(run.created_at);
      const pad = (n: number) => String(n).padStart(2, "0");
      link.href = url;
      link.download = `agent-evaluation-${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-${pad(stamp.getHours())}${pad(stamp.getMinutes())}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setError(null);
    } catch (err: any) {
      setError("Could not download the evaluation report");
    } finally {
      setDownloading(null);
    }
  }

  const trigger = useMutation({
    mutationFn: async () => (await api.post("/observability/eval-runs/run")).data,
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval-status"] });
    },
    onError: (err: any) => {
      setError(err?.response?.data?.detail || "Failed to trigger eval run — requires the model_admin role");
      qc.invalidateQueries({ queryKey: ["eval-status"] });
    },
  });

  return (
    <div>
      <p className="muted" style={{ fontSize: 13 }}>
        Runs a fixed set of labelled scenarios against the specialist agents and scores their behaviour against
        known-correct expectations (e.g. "must flag a HIGH finding when telemetry is mostly stale"). Behavioral
        cases are skipped, not failed, when the active model is the mock gateway — mock fills only schema-required
        fields and can never reason about the scenario.
      </p>
      {error && <div className="error-banner">{error}</div>}
      <div className="row" style={{ marginBottom: 16, gap: 12 }}>
        <button onClick={() => trigger.mutate()} disabled={trigger.isPending || running}>
          {trigger.isPending ? "Queuing..." : running ? "Evaluation running…" : "Run eval suite"}
        </button>
        {running && (
          <span className="muted" style={{ fontSize: 13 }}>
            Started {status?.started_at ? new Date(status.started_at).toLocaleTimeString() : ""} — this takes a few minutes
            with a live model; the result appears here when it finishes.
          </span>
        )}
      </div>

      {isLoading && <div className="empty-state">Loading...</div>}
      {runs && runs.length === 0 && <div className="empty-state">No eval runs yet.</div>}
      {runs?.map((run) => (
        <div key={run.id} className="card" style={{ marginBottom: 12 }}>
          <div className="row-between" style={{ cursor: "pointer" }} onClick={() => setExpanded(expanded === run.id ? null : run.id)}>
            <div>
              <strong>{run.total_cases === 0 ? "Not scored" : `${run.passed_cases}/${run.total_cases} passed`}</strong>{" "}
              <span className="muted">
                · {run.model_provider} · {run.triggered_by || "system"} · {new Date(run.created_at).toLocaleString()}
                {run.results.some((r) => r.outcome === "skipped") && ` · ${run.results.filter((r) => r.outcome === "skipped").length} not scored`}
              </span>
            </div>
            <div className="row" style={{ gap: 10, flexWrap: "nowrap" }}>
              <Badge text={run.total_cases === 0 ? "not scored" : run.passed_cases === run.total_cases ? "all passed" : "some failed"} />
              <button className="secondary" disabled={downloading === run.id}
                      onClick={(e) => { e.stopPropagation(); downloadRun(run); }}>
                {downloading === run.id ? "Preparing…" : "Download Excel"}
              </button>
            </div>
          </div>
          {expanded === run.id && (
            <div className="evidence-box" style={{ marginTop: 10 }}>
              {run.results.map((r, i) => (
                <div key={i} className="finding">
                  <Badge text={r.outcome} /> <strong>{r.case}</strong> <span className="muted">({r.agent}, {r.tier})</span>
                  <div className="muted" style={{ fontSize: 13 }}>{r.detail}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function AgentObservability() {
  const [tab, setTab] = useState<"metrics" | "eval">("metrics");

  return (
    <div>
      <h2 className="page-title">Agent Observability</h2>
      <p className="page-intro">
        Live call health for every specialist agent and the document-intake vision path, plus a fixed-scenario
        evaluation suite — the workspace behind the model_admin role's registry/eval/deploy permissions.
      </p>
      <div className="tabs">
        <div className={"tab" + (tab === "metrics" ? " active" : "")} onClick={() => setTab("metrics")}>Call Metrics</div>
        <div className={"tab" + (tab === "eval" ? " active" : "")} onClick={() => setTab("eval")}>Evaluation</div>
      </div>
      {tab === "metrics" ? <CallMetricsTab /> : <EvaluationTab />}
    </div>
  );
}
