import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";
import DecisionDrawer from "../components/DecisionDrawer";

interface AssetSnapshot { id: string; name: string; asset_type: string }
interface SiteSnapshot { id: string; name: string; assets: AssetSnapshot[] }
interface PortfolioSnapshot { id: string; name: string; sites: SiteSnapshot[] }

interface CustomerOption { id: string; name: string; siteName: string }

interface ActionTicket {
  id: string; action_type: string; asset_id: string | null; asset_name: string | null;
  quantity: number; unit: string; risk_level: string; requires_approval: boolean; reason: string | null;
  start_time: string; end_time: string; decision_id: string; decision_cycle_id: string;
  decision_status: string; ticket_status: string; signal_state: string | null;
  approval_outcome: string | null; created_at: string;
}

// "Customer" here maps to the platform's consumer-type assets (industrial /
// commercial load sites) — the demand side the utility is managing, as
// opposed to its own generation/storage fleet. There's no separate retail
// "Customer" entity in the canonical model (see docs/DATA_INGESTION.md), so
// this groups on Asset.asset_type === "consumer", which is the concept that
// actually exists and actually has governed actions taken against it
// (demand response, curtailment) that a decision can be explained for.
export default function CustomerDecisions() {
  const [customerId, setCustomerId] = useState<string>("");
  const [selectedDecision, setSelectedDecision] = useState<string | null>(null);

  const { data: portfolios, isLoading: portfoliosLoading } = useQuery<PortfolioSnapshot[]>({
    queryKey: ["twin-portfolio"],
    queryFn: async () => (await api.get("/twin/portfolio")).data,
  });

  const customers: CustomerOption[] = useMemo(() => {
    const out: CustomerOption[] = [];
    for (const p of portfolios || []) {
      for (const s of p.sites) {
        for (const a of s.assets) {
          if (a.asset_type === "consumer") out.push({ id: a.id, name: a.name, siteName: s.name });
        }
      }
    }
    return out;
  }, [portfolios]);

  const { data: actions, isLoading: actionsLoading, error } = useQuery<ActionTicket[]>({
    queryKey: ["customer-actions", customerId],
    queryFn: async () => (await api.get("/actions", { params: { asset_id: customerId, limit: 100 } })).data,
    enabled: !!customerId,
  });

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Customer Decisions</h2>
      <p className="muted">
        Pick a demand customer (consumer-side asset) to see every governed decision that has affected them —
        curtailment, demand response, or a hold — with the operator-facing explanation for why.
      </p>

      <div className="field" style={{ maxWidth: 420 }}>
        <label>Customer</label>
        <select value={customerId} onChange={(e) => { setCustomerId(e.target.value); setSelectedDecision(null); }}>
          <option value="">{portfoliosLoading ? "Loading customers..." : "Select a customer"}</option>
          {customers.map((c) => (
            <option key={c.id} value={c.id}>{c.name} — {c.siteName}</option>
          ))}
        </select>
      </div>

      {!customerId && <div className="empty-state">Select a customer above to see their decision history.</div>}
      {customerId && actionsLoading && <div className="empty-state">Loading decision history...</div>}
      {customerId && error && <div className="error-banner">Failed to load decisions for this customer.</div>}
      {customerId && actions && actions.length === 0 && (
        <div className="empty-state">No governed actions have been taken for this customer yet.</div>
      )}

      {customerId && actions && actions.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>When</th><th>Action</th><th>Quantity</th><th>Risk</th><th>Status</th><th>Reason</th><th>Decision Cycle</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => (
                <tr key={a.id} onClick={() => setSelectedDecision(a.decision_id)} style={{ cursor: "pointer" }}>
                  <td>{new Date(a.start_time).toLocaleString()}</td>
                  <td><Badge text={a.action_type} /></td>
                  <td>{a.quantity.toLocaleString(undefined, { maximumFractionDigits: 0 })} {a.unit}</td>
                  <td><Badge text={a.risk_level} /></td>
                  <td><Badge text={a.ticket_status} /></td>
                  <td style={{ maxWidth: 360 }}>{a.reason || "—"}</td>
                  <td className="mono">{a.decision_cycle_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedDecision && <DecisionDrawer id={selectedDecision} onClose={() => setSelectedDecision(null)} />}
    </div>
  );
}
