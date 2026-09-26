import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import Badge from "../components/Badge";
import DecisionSummary from "../components/DecisionSummary";
import PortfolioTrendChart from "../components/PortfolioTrendChart";

interface AssetSnapshot {
  id: string;
  name: string;
  asset_type: string;
  rated_capacity_kw: number;
  latest: Record<string, { value: number; unit: string; freshness: string; confidence: number; age_seconds: number }>;
}
interface SiteSnapshot { id: string; name: string; assets: AssetSnapshot[] }
interface PortfolioSnapshot { id: string; name: string; sites: SiteSnapshot[] }
interface Battery { asset_id: string; soc_pct: number; soh_pct: number; power_limit_kw: number; soc_min_pct: number; soc_max_pct: number }

interface OperationsStatus {
  operating_state: "idle" | "running";
  operating_state_changed_at: string | null;
  has_data_source: boolean;
  active_connector_count: number;
  document_count: number;
}

const TYPE_ICON: Record<string, string> = { solar: "☀️", wind: "💨", battery: "🔋", consumer: "🏭", grid_interconnection: "⚡" };

function fmt(v: number | undefined, digits = 0) {
  if (v === undefined) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export default function PortfolioOperations() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const { data: ops, isLoading: opsLoading, error: opsError } = useQuery<OperationsStatus>({
    queryKey: ["operations-status"],
    queryFn: async () => (await api.get("/operations/status")).data,
    refetchInterval: 8000,
  });
  const running = ops?.operating_state === "running";

  const start = useMutation({
    mutationFn: async () => (await api.post("/operations/start")).data,
    onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ["operations-status"] }); },
    onError: (err: any) => setError(err?.response?.data?.detail || "Failed to start the optimizer"),
  });
  const stop = useMutation({
    mutationFn: async () => (await api.post("/operations/stop")).data,
    onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ["operations-status"] }); },
    onError: (err: any) => setError(err?.response?.data?.detail || "Failed to stop the optimizer"),
  });

  const { data: portfolios, isLoading, error: portfolioError } = useQuery<PortfolioSnapshot[]>({
    queryKey: ["twin-portfolio"],
    queryFn: async () => (await api.get("/twin/portfolio")).data,
    refetchInterval: 10000,
    enabled: running,
  });
  const { data: batteries } = useQuery<Battery[]>({
    queryKey: ["twin-batteries"],
    queryFn: async () => (await api.get("/twin/batteries")).data,
    refetchInterval: 10000,
    enabled: running,
  });

  const batteryByAsset = Object.fromEntries((batteries || []).map((b) => [b.asset_id, b]));

  let totalGenKw = 0, totalDemandKw = 0, totalBatteryKw = 0;
  for (const p of portfolios || []) {
    for (const s of p.sites) {
      for (const a of s.assets) {
        const power = a.latest["power_kw"]?.value ?? 0;
        if (a.asset_type === "solar" || a.asset_type === "wind") totalGenKw += power;
        else if (a.asset_type === "consumer") totalDemandKw += Math.abs(power);
        else if (a.asset_type === "battery") totalBatteryKw += power;
      }
    }
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="row-between" style={{ flexWrap: "wrap", gap: 10 }}>
          <div>
            <div className="row" style={{ gap: 8 }}>
              <Badge text={ops?.operating_state || "idle"} />
              <strong style={{ fontSize: 14 }}>{running ? "Optimizer running" : "Optimizer idle"}</strong>
            </div>
            <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>
              {running
                ? "The decision cycle is live — cards, trend and recent decisions below reflect real analysis."
                : opsLoading
                  ? "Checking optimizer status..."
                  : opsError || !ops
                    ? "Could not check optimizer status — try reloading the page."
                    : !ops.has_data_source
                      ? "Activate a database or data_table connector in Connector Studio to enable Start Optimizer (Document Intake uploads still ingest data, but no longer unlock this on their own)."
                      : "A database/data_table connector is active — click Start Optimizer to begin analysis, decisions and actions."}
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="secondary" onClick={() => navigate("/documents")}>Document Intake</button>
            {running ? (
              <button className="secondary" onClick={() => stop.mutate()} disabled={stop.isPending}>
                {stop.isPending ? "Stopping..." : "Stop Optimizer"}
              </button>
            ) : (
              <button onClick={() => start.mutate()} disabled={start.isPending || opsLoading || !ops?.has_data_source}>
                {start.isPending ? "Starting..." : "Start Optimizer"}
              </button>
            )}
          </div>
        </div>
        {error && <div className="error-banner" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
      </div>

      {!running && (
        <div className="empty-state">
          {opsLoading
            ? "Checking optimizer status..."
            : "Nothing to show yet — start the optimizer above once a data source is connected to begin filling in live generation, demand, trend and decision data."}
        </div>
      )}

      {running && (
        <>
          {isLoading && <div className="empty-state">Loading portfolio...</div>}
          {portfolioError && <div className="error-banner">Failed to load portfolio snapshot.</div>}

          {!isLoading && !portfolioError && (
            <>
              <div className="grid grid-cards" style={{ marginBottom: 20 }}>
                <div className="card">
                  <h3>Total Generation</h3>
                  <div className="big">{fmt(totalGenKw)} kW</div>
                </div>
                <div className="card">
                  <h3>Total Demand</h3>
                  <div className="big">{fmt(totalDemandKw)} kW</div>
                </div>
                <div className="card">
                  <h3>Battery Net Power</h3>
                  <div className="big">{fmt(totalBatteryKw)} kW</div>
                </div>
                <div className="card">
                  <h3>Batteries</h3>
                  <div className="big">{(batteries || []).map((b) => `${fmt(b.soc_pct, 0)}%`).join(" / ") || "—"}</div>
                </div>
              </div>

              <PortfolioTrendChart />

              <DecisionSummary />

              {(portfolios || []).map((p) => (
                <div key={p.id} style={{ marginBottom: 24 }}>
                  <h2 style={{ fontSize: 15 }}>{p.name}</h2>
                  {p.sites.map((site) => (
                    <div key={site.id} style={{ marginBottom: 16 }}>
                      <div className="muted" style={{ marginBottom: 8, fontSize: 13 }}>{site.name}</div>
                      <div className="grid grid-cards">
                        {site.assets.map((a) => {
                          const power = a.latest["power_kw"];
                          const battery = batteryByAsset[a.id];
                          return (
                            <div className="card" key={a.id}>
                              <div className="row-between">
                                <h3 style={{ textTransform: "none" }}>
                                  {TYPE_ICON[a.asset_type] || ""} {a.name}
                                </h3>
                                {power && <Badge text={power.freshness} />}
                              </div>
                              <div className="big">{power ? `${fmt(power.value)} kW` : "no data"}</div>
                              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                                rated {fmt(a.rated_capacity_kw)} kW
                                {battery && ` · SoC ${fmt(battery.soc_pct)}% (${battery.soc_min_pct}-${battery.soc_max_pct}%) · SoH ${fmt(battery.soh_pct)}%`}
                              </div>
                              {Object.entries(a.latest)
                                .filter(([k]) => k !== "power_kw")
                                .map(([k, v]) => (
                                  <div key={k} className="muted" style={{ fontSize: 11 }}>
                                    {k}: {fmt(v.value, 2)} {v.unit}
                                  </div>
                                ))}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </>
          )}
        </>
      )}
    </div>
  );
}
