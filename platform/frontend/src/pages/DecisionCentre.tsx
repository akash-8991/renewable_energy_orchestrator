import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";
import DecisionDrawer from "../components/DecisionDrawer";

interface DecisionSummary {
  id: string; decision_cycle_id: string; version: number; trigger: string; status: string;
  autonomy_mode: string; confidence: number; binding_constraints: string[]; risk_flags: any[];
  created_at: string; expires_at: string | null;
}

export default function DecisionCentre() {
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery<DecisionSummary[]>({
    queryKey: ["decisions", statusFilter],
    queryFn: async () => (await api.get("/decisions", { params: statusFilter ? { status: statusFilter } : {} })).data,
    refetchInterval: 15000,
  });

  return (
    <div>
      <div className="row-between" style={{ marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, margin: 0 }}>Decision Ledger</h2>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">all statuses</option>
          <option value="proposed">proposed</option>
          <option value="failed">failed</option>
          <option value="executed">executed</option>
        </select>
      </div>
      {isLoading && <div className="empty-state">Loading...</div>}
      {error && <div className="error-banner">Failed to load decisions.</div>}
      {data && (
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Created</th><th>Cycle</th><th>Trigger</th><th>Status</th><th>Mode</th><th>Confidence</th><th>Risk Flags</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.id} onClick={() => setSelected(d.id)} style={{ cursor: "pointer" }}>
                <td>{new Date(d.created_at).toLocaleString()}</td>
                <td className="mono">{d.decision_cycle_id}</td>
                <td>{d.trigger}</td>
                <td><Badge text={d.status} /></td>
                <td><Badge text={d.autonomy_mode} /></td>
                <td>{(d.confidence * 100).toFixed(0)}%</td>
                <td>{d.risk_flags.length > 0 ? <Badge text={`${d.risk_flags.length} flag(s)`} /> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {data && data.length === 0 && <div className="empty-state">No decisions yet — the optimizer cycles every 2 minutes.</div>}
      {selected && <DecisionDrawer id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
