import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import Badge from "../components/Badge";

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

const TYPE_ICON: Record<string, string> = { solar: "☀️", wind: "💨", battery: "🔋", consumer: "🏭", grid_interconnection: "⚡" };

function fmt(v: number | undefined, digits = 0) {
  if (v === undefined) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export default function PortfolioOperations() {
  const { data: portfolios, isLoading, error } = useQuery<PortfolioSnapshot[]>({
    queryKey: ["twin-portfolio"],
    queryFn: async () => (await api.get("/twin/portfolio")).data,
    refetchInterval: 10000,
  });
  const { data: batteries } = useQuery<Battery[]>({
    queryKey: ["twin-batteries"],
    queryFn: async () => (await api.get("/twin/batteries")).data,
    refetchInterval: 10000,
  });

  if (isLoading) return <div className="empty-state">Loading portfolio...</div>;
  if (error) return <div className="error-banner">Failed to load portfolio snapshot.</div>;

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
    </div>
  );
}
