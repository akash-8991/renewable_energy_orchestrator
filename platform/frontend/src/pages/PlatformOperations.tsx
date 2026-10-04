import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import Badge from "../components/Badge";

async function fetchHealth(url: string): Promise<{ ok: boolean; detail: string }> {
  try {
    const { data } = await api.get(url);
    return { ok: true, detail: JSON.stringify(data) };
  } catch (e: any) {
    return { ok: false, detail: e?.message || "unreachable" };
  }
}

export default function PlatformOperations() {
  const { data: apiHealth } = useQuery({ queryKey: ["health-api"], queryFn: () => fetchHealth("/health"), refetchInterval: 15000 });
  const { data: decisions } = useQuery({ queryKey: ["decisions", "all"], queryFn: async () => (await api.get("/decisions", { params: { limit: 500 } })).data, refetchInterval: 20000 });
  const { data: connectors } = useQuery({ queryKey: ["connectors"], queryFn: async () => (await api.get("/connectors")).data, refetchInterval: 20000 });
  const { data: signals } = useQuery({ queryKey: ["signals"], queryFn: async () => (await api.get("/signals")).data, refetchInterval: 20000 });

  const proposedCount = (decisions || []).filter((d: any) => d.status === "proposed").length;
  const failedCount = (decisions || []).filter((d: any) => d.status === "failed").length;
  const activeConnectors = (connectors || []).filter((c: any) => c.status === "active").length;
  const acknowledgedSignals = (signals || []).filter((s: any) => s.state === "acknowledged").length;
  const rejectedSignals = (signals || []).filter((s: any) => s.state === "rejected" || s.state === "timed_out").length;

  return (
    <div>
      <h2 className="page-title">Platform Operations</h2>
      <p className="page-intro">Live health of the platform's services.</p>
      <div className="grid grid-cards">
        <div className="card">
          <h3>API</h3>
          <Badge text={apiHealth?.ok ? "healthy" : "unreachable"} />
        </div>
        <div className="card">
          <h3>Decisions (last 500)</h3>
          <div className="big">{decisions?.length ?? "—"}</div>
          <div className="muted">{proposedCount} proposed · {failedCount} failed</div>
        </div>
        <div className="card">
          <h3>Active connectors</h3>
          <div className="big">{activeConnectors}</div>
          <div className="muted">{connectors?.length ?? 0} total registered</div>
        </div>
        <div className="card">
          <h3>Signals</h3>
          <div className="big">{acknowledgedSignals}</div>
          <div className="muted">acknowledged · {rejectedSignals} rejected/timed out</div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Service topology</h3>
        <p className="card-help" style={{ marginBottom: 0 }}>
          api (this dashboard's backend) · optimizer-worker (decision cycles, MILP solve) · agent-worker (LLM evidence
          pass) · ot-gateway-sim (independent OT command validation) · edge-simulator (synthetic telemetry) ·
          export-worker (governed Excel generation). Background workers report liveness through heartbeats; their
          output shows up on this page and in the Decision Centre as fresh Decisions and Signals.
        </p>
      </div>
    </div>
  );
}
