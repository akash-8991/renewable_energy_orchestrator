import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
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

function fmt(v: number, digits = 0) {
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

// The platform's governed-decision machinery (Decision/Action) operates on
// Assets, not retail accounts — an industrial "consumer" Asset (Industrial
// Estate Line 1 etc.) is the utility's own demand-side portfolio, distinct
// from the 100 individually metered retail/SME/industrial customer accounts
// ingested from the reference dataset (docs/DATA_INGESTION.md). Assets can
// have governed decisions explained for them; retail customers can't (no
// Action ever targets a customer_id) but do have real consumption/cost
// insights — hence two tabs covering two genuinely different things under
// one "Customers" workspace, rather than forcing them into one list.
function GovernedAssetsTab() {
  const [customerId, setCustomerId] = useState<string>("");
  const [selectedDecision, setSelectedDecision] = useState<string | null>(null);

  const { data: portfolios, isLoading: portfoliosLoading } = useQuery<PortfolioSnapshot[]>({
    queryKey: ["twin-portfolio"],
    queryFn: async () => (await api.get("/twin/portfolio")).data,
  });

  const consumerAssets: CustomerOption[] = useMemo(() => {
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
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Pick one of the utility's own demand-side assets (industrial/commercial load sites) to see every
        governed decision that has affected it — curtailment, demand response, or a hold — with the
        operator-facing explanation for why.
      </p>

      <div className="field" style={{ maxWidth: 420 }}>
        <label>Asset</label>
        <select value={customerId} onChange={(e) => { setCustomerId(e.target.value); setSelectedDecision(null); }}>
          <option value="">{portfoliosLoading ? "Loading..." : "Select an asset"}</option>
          {consumerAssets.map((c) => (
            <option key={c.id} value={c.id}>{c.name} — {c.siteName}</option>
          ))}
        </select>
      </div>

      {!customerId && <div className="empty-state">Select an asset above to see its decision history.</div>}
      {customerId && actionsLoading && <div className="empty-state">Loading decision history...</div>}
      {customerId && error && <div className="error-banner">Failed to load decisions for this asset.</div>}
      {customerId && actions && actions.length === 0 && (
        <div className="empty-state">No governed actions have been taken for this asset yet.</div>
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

interface CustomerFilters { customer_types: string[]; regions: string[] }

interface CustomerSummary {
  id: string; customer_ref: string; customer_type: string; region: string;
  annual_consumption_kwh: number; renewable_profile: string | null;
  solar_capacity_kw: number; wind_capacity_kw: number;
  battery_installed: boolean; battery_capacity_kwh: number; tariff_plan: string | null;
}

interface DailyPoint {
  date: string; consumption_kwh: number; solar_generation_kwh: number; wind_generation_kwh: number;
  net_grid_import_kwh: number; export_kwh: number; avg_energy_rate_gbp_kwh: number; estimated_cost_gbp: number;
}

interface CustomerInsights {
  customer: CustomerSummary;
  total_consumption_kwh: number;
  total_renewable_generation_kwh: number;
  renewable_share_pct: number;
  total_estimated_cost_gbp: number;
  avg_daily_cost_gbp: number;
  days_covered: number;
  daily: DailyPoint[];
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

function CustomerInsightsTab() {
  const [customerType, setCustomerType] = useState("");
  const [region, setRegion] = useState("");
  const [customerRefFilter, setCustomerRefFilter] = useState("");
  const [selectedRef, setSelectedRef] = useState<string | null>(null);

  const { data: filters } = useQuery<CustomerFilters>({
    queryKey: ["customer-filters"],
    queryFn: async () => (await api.get("/customers/filters")).data,
  });

  const { data: customers, isLoading, error } = useQuery<CustomerSummary[]>({
    queryKey: ["customers", customerType, region, customerRefFilter],
    queryFn: async () =>
      (await api.get("/customers", {
        params: {
          ...(customerType ? { customer_type: customerType } : {}),
          ...(region ? { region } : {}),
          ...(customerRefFilter ? { customer_ref: customerRefFilter } : {}),
        },
      })).data,
  });

  const { data: insights, isLoading: insightsLoading } = useQuery<CustomerInsights>({
    queryKey: ["customer-insights", selectedRef],
    queryFn: async () => (await api.get(`/customers/${selectedRef}/insights`)).data,
    enabled: !!selectedRef,
  });

  return (
    <div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Retail and individual customer accounts (residential, SME, industrial) ingested from the reference
        dataset — filter by customer type, region, or customer ID, then pick one for their consumption,
        renewable generation and cost trend.
      </p>

      <div className="row" style={{ gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Customer type</label>
          <select value={customerType} onChange={(e) => setCustomerType(e.target.value)}>
            <option value="">all types</option>
            {(filters?.customer_types || []).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Region</label>
          <select value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="">all regions</option>
            {(filters?.regions || []).map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Customer ID</label>
          <input placeholder="e.g. CUST_014" value={customerRefFilter} onChange={(e) => setCustomerRefFilter(e.target.value)} />
        </div>
      </div>

      {isLoading && <div className="empty-state">Loading customers...</div>}
      {error && <div className="error-banner">Failed to load customers.</div>}
      {customers && customers.length === 0 && <div className="empty-state">No customers match these filters.</div>}
      {customers && customers.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Customer</th><th>Type</th><th>Region</th><th>Tariff</th><th>Renewable Profile</th><th>Annual Use</th><th>Battery</th></tr>
            </thead>
            <tbody>
              {customers.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setSelectedRef(c.customer_ref)}
                  style={{ cursor: "pointer", background: selectedRef === c.customer_ref ? "var(--bg-panel-2)" : undefined }}
                >
                  <td className="mono">{c.customer_ref}</td>
                  <td><Badge text={c.customer_type} /></td>
                  <td>{c.region}</td>
                  <td>{c.tariff_plan || "—"}</td>
                  <td>{c.renewable_profile || "—"}</td>
                  <td>{fmt(c.annual_consumption_kwh)} kWh</td>
                  <td>{c.battery_installed ? `${fmt(c.battery_capacity_kwh)} kWh` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedRef && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="row-between">
            <h3 style={{ textTransform: "none", fontSize: 14 }}>{selectedRef} — Insights</h3>
            <button className="ghost" onClick={() => setSelectedRef(null)}>close ✕</button>
          </div>
          {insightsLoading && <div className="empty-state">Loading insights...</div>}
          {insights && insights.days_covered === 0 && (
            <div className="empty-state">No consumption history ingested for this customer.</div>
          )}
          {insights && insights.days_covered > 0 && (
            <>
              <div className="row" style={{ gap: 32, flexWrap: "wrap", margin: "14px 0 20px" }}>
                <Stat label="Total Consumption" value={`${fmt(insights.total_consumption_kwh)} kWh`} />
                <Stat label="Renewable Share" value={`${insights.renewable_share_pct.toFixed(1)}%`} />
                <Stat label="Estimated Cost" value={`£${fmt(insights.total_estimated_cost_gbp)}`} />
                <Stat label="Avg Daily Cost" value={`£${insights.avg_daily_cost_gbp.toFixed(2)}`} />
                <Stat label="Days Covered" value={String(insights.days_covered)} />
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={insights.daily} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="custConsumption" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#2563eb" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#2563eb" stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="custRenewable" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#16a34a" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#16a34a" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e1e5ec" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#667085" }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 11, fill: "#667085" }} width={50} label={{ value: "kWh", angle: -90, position: "insideLeft", fontSize: 11, fill: "#667085" }} />
                  <Tooltip
                    contentStyle={{ background: "#fff", border: "1px solid #e1e5ec", borderRadius: 8, fontSize: 12 }}
                    formatter={(value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 1 }) + " kWh"}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area type="monotone" dataKey="consumption_kwh" name="Consumption" stroke="#2563eb" fill="url(#custConsumption)" strokeWidth={2} />
                  <Area
                    type="monotone"
                    dataKey={(d: DailyPoint) => d.solar_generation_kwh + d.wind_generation_kwh}
                    name="Renewable generation"
                    stroke="#16a34a"
                    fill="url(#custRenewable)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function CustomerDecisions() {
  const [tab, setTab] = useState<"assets" | "insights">("insights");

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Customers</h2>
      <div className="tabs">
        <div className={"tab" + (tab === "insights" ? " active" : "")} onClick={() => setTab("insights")}>Customer Insights</div>
        <div className={"tab" + (tab === "assets" ? " active" : "")} onClick={() => setTab("assets")}>Governed Assets</div>
      </div>
      {tab === "insights" ? <CustomerInsightsTab /> : <GovernedAssetsTab />}
    </div>
  );
}
