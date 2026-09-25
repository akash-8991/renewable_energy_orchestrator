import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface ActionTicket {
  id: string; action_type: string; asset_id: string | null; asset_name: string | null; quantity: number;
  unit: string; risk_level: string; requires_approval: boolean; reason: string | null; start_time: string;
  end_time: string; decision_id: string; decision_cycle_id: string; decision_status: string;
  ticket_status: string; signal_state: string | null; approval_outcome: string | null; created_at: string;
}

const CSV_COLUMNS: (keyof ActionTicket)[] = [
  "id", "action_type", "asset_name", "quantity", "unit", "risk_level", "requires_approval",
  "ticket_status", "signal_state", "approval_outcome", "decision_cycle_id", "reason", "start_time", "end_time",
];

function toCsv(rows: ActionTicket[]): string {
  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = CSV_COLUMNS.join(",");
  const body = rows.map((r) => CSV_COLUMNS.map((c) => escape(r[c])).join(",")).join("\n");
  return `${header}\n${body}`;
}

function downloadCsv(rows: ActionTicket[]) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `action-tickets-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ActionLog() {
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  const { data, isLoading, error } = useQuery<ActionTicket[]>({
    queryKey: ["actions", statusFilter, typeFilter],
    queryFn: async () =>
      (await api.get("/actions", {
        params: { ...(statusFilter ? { status: statusFilter } : {}), ...(typeFilter ? { action_type: typeFilter } : {}) },
      })).data,
    refetchInterval: 20000,
  });

  return (
    <div>
      <div className="row-between" style={{ marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ fontSize: 15, margin: 0 }}>Action Tickets</h2>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="">all types</option>
            <option value="charge">charge</option>
            <option value="discharge">discharge</option>
            <option value="buy">buy</option>
            <option value="sell">sell</option>
            <option value="curtail">curtail</option>
            <option value="demand_response">demand_response</option>
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">all statuses</option>
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="dispatching">dispatching</option>
            <option value="dispatched">dispatched</option>
            <option value="rejected">rejected</option>
            <option value="failed">failed</option>
            <option value="held">held</option>
            <option value="expired">expired</option>
          </select>
          <button className="secondary" onClick={() => data && downloadCsv(data)} disabled={!data || data.length === 0}>
            Export CSV
          </button>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Every governed action any decision cycle has ever proposed, as one ticket each — battery charge/discharge,
        grid buy/sell, curtailment, demand response — with its resolved disposition. For a full Excel evidence pack
        (including reasoning, approvals and OT acknowledgements), use Audit &amp; Exports.
      </p>

      {isLoading && <div className="empty-state">Loading...</div>}
      {error && <div className="error-banner">Failed to load action tickets.</div>}
      {data && data.length === 0 && <div className="empty-state">No actions match these filters.</div>}
      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Started</th><th>Type</th><th>Asset</th><th>Qty</th><th>Risk</th><th>Ticket Status</th><th>Decision Cycle</th><th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {data.map((t) => (
              <tr key={t.id}>
                <td className="mono" style={{ fontSize: 11 }}>{new Date(t.start_time).toLocaleString()}</td>
                <td>{t.action_type}</td>
                <td>{t.asset_name || t.asset_id || "—"}</td>
                <td>{t.quantity.toFixed(0)} {t.unit}</td>
                <td><Badge text={t.risk_level} /></td>
                <td><Badge text={t.ticket_status} /></td>
                <td className="mono muted" style={{ fontSize: 11 }}>{t.decision_cycle_id}</td>
                <td className="muted" style={{ fontSize: 12, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.reason || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
