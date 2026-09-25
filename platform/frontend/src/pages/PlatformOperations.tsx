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
      <h2 style={{ fontSize: 15 }}>Platform Operations</h2>
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
        <p className="muted" style={{ fontSize: 13 }}>
          api (this dashboard's backend) · optimizer-worker (10-min decision cycle, MILP solve) · agent-worker (9-agent
          LLM evidence pass) · ot-gateway-sim (independent OT command validation, network-isolated) · edge-simulator
          (synthetic telemetry) · export-worker (governed Excel generation). Background workers have no HTTP surface
          by design — their liveness shows up as fresh Decisions/Signals appearing on this page and in the Decision
          Centre, not as a health endpoint here.
        </p>
      </div>
    </div>
  );
}
