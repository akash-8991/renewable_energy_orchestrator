import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import Badge from "./Badge";
import DecisionDrawer from "./DecisionDrawer";

interface DecisionSummaryRow {
  id: string; decision_cycle_id: string; trigger: string; status: string;
  autonomy_mode: string; confidence: number; binding_constraints: string[]; risk_flags: any[];
  created_at: string;
}

const REFRESH_MS = 5 * 60 * 1000;

export default function DecisionSummary() {
  const [selected, setSelected] = useState<string | null>(null);

  const { data, isLoading, error, dataUpdatedAt } = useQuery<DecisionSummaryRow[]>({
    queryKey: ["decisions-summary"],
    queryFn: async () => (await api.get("/decisions", { params: { limit: 5 } })).data,
    refetchInterval: REFRESH_MS,
  });

  return (
    <div style={{ marginBottom: 20 }}>
      <div className="card">
        <div className="row-between" style={{ marginBottom: 4, flexWrap: "wrap", gap: 8 }}>
          <h3 style={{ margin: 0 }}>Recent Decisions</h3>
          <div className="row" style={{ gap: 10 }}>
            {dataUpdatedAt > 0 && (
              <span className="muted" style={{ fontSize: 11 }}>
                updated {new Date(dataUpdatedAt).toLocaleTimeString()} · refreshes every 5 min
              </span>
            )}
            <Link to="/decisions" style={{ fontSize: 12 }}>View all →</Link>
          </div>
        </div>

        {isLoading && <div className="empty-state">Loading recent decisions...</div>}
        {error && <div className="error-banner">Failed to load recent decisions.</div>}
        {data && data.length === 0 && (
          <div className="empty-state">No decisions yet — the optimizer cycles every 2 minutes.</div>
        )}
        {data && data.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Created</th><th>Cycle</th><th>Trigger</th><th>Status</th><th>Mode</th><th>Confidence</th><th>Risk Flags</th></tr>
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
      </div>

      {selected && <DecisionDrawer id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
