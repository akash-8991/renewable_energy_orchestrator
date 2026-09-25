import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface Signal {
  id: string; correlation_id: string; target_asset_id: string; command_type: string;
  setpoint_value: number; unit: string; state: string; state_history: { state: string; at: string; note?: string }[];
  created_at: string; updated_at: string;
}

export default function LiveSignalMonitor() {
  const { data, isLoading } = useQuery<Signal[]>({
    queryKey: ["signals"],
    queryFn: async () => (await api.get("/signals")).data,
    refetchInterval: 8000,
  });

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Live Signal Monitor</h2>
      <p className="muted">Every OT command signal from prepared through acknowledged/rejected/rolled-back.</p>
      {isLoading && <div className="empty-state">Loading...</div>}
      {data && data.length === 0 && <div className="empty-state">No signals yet — enable APPROVAL_REQUIRED or AUTONOMOUS_BOUNDED mode in Policy Studio to see dispatchable actions.</div>}
      {data && data.length > 0 && (
        <div className="table-scroll">
        <table>
          <thead>
            <tr><th>Updated</th><th>Asset</th><th>Command</th><th>Setpoint</th><th>State</th><th>History</th></tr>
          </thead>
          <tbody>
            {data.map((s) => (
              <tr key={s.id}>
                <td>{new Date(s.updated_at).toLocaleTimeString()}</td>
                <td className="mono">{s.target_asset_id.slice(0, 8)}</td>
                <td>{s.command_type}</td>
                <td>{s.setpoint_value} {s.unit}</td>
                <td><Badge text={s.state} /></td>
                <td className="muted" style={{ fontSize: 11 }}>
                  {s.state_history.map((h) => h.state).join(" → ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
