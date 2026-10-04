import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../api/client";
import Badge from "./Badge";

interface Site { id: string; name: string; latitude: number | null; longitude: number | null; market_area: string }
interface BatterySpec {
  energy_capacity_kwh: number; power_limit_kw: number; soc_min_pct: number; soc_max_pct: number;
  soc_current_pct: number; soh_pct: number; round_trip_efficiency: number;
}
interface Asset {
  id: string; site_id: string; site_name: string | null; name: string; asset_type: string;
  rated_capacity_kw: number; retired: boolean; battery: BatterySpec | null;
}

const TYPES = ["solar", "wind", "battery", "consumer", "grid_interconnection"];

export default function PortfolioRegistry() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [siteName, setSiteName] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [assetName, setAssetName] = useState("");
  const [siteId, setSiteId] = useState("");
  const [type, setType] = useState("solar");
  const [capacity, setCapacity] = useState("");
  const [energy, setEnergy] = useState("");

  const { data: sites } = useQuery<Site[]>({ queryKey: ["registry-sites"], queryFn: async () => (await api.get("/admin/portfolio/sites")).data });
  const { data: assets } = useQuery<Asset[]>({ queryKey: ["registry-assets"], queryFn: async () => (await api.get("/admin/portfolio/assets")).data });

  const refresh = () => {
    setError(null);
    qc.invalidateQueries({ queryKey: ["registry-sites"] });
    qc.invalidateQueries({ queryKey: ["registry-assets"] });
  };
  const onError = (err: any) => {
    const d = err?.response?.data?.detail;
    setError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => `${x.loc?.slice(-1)}: ${x.msg}`).join("; ") : "Request failed");
  };

  const addSite = useMutation({
    mutationFn: async () => (await api.post("/admin/portfolio/sites", {
      name: siteName, latitude: lat === "" ? null : Number(lat), longitude: lon === "" ? null : Number(lon),
    })).data,
    onSuccess: () => { setSiteName(""); setLat(""); setLon(""); refresh(); },
    onError,
  });
  const addAsset = useMutation({
    mutationFn: async () => {
      const kw = Number(capacity);
      const body: any = { site_id: siteId || sites?.[0]?.id, name: assetName, asset_type: type, rated_capacity_kw: kw };
      if (type === "battery") body.battery = { energy_capacity_kwh: Number(energy), power_limit_kw: kw };
      return (await api.post("/admin/portfolio/assets", body)).data;
    },
    onSuccess: () => { setAssetName(""); setCapacity(""); setEnergy(""); refresh(); },
    onError,
  });
  const retire = useMutation({
    mutationFn: async (v: { id: string; retired: boolean }) => (await api.patch(`/admin/portfolio/assets/${v.id}`, { retired: v.retired })).data,
    onSuccess: refresh,
    onError,
  });

  const submit = (fn: () => void) => (e: FormEvent) => { e.preventDefault(); fn(); };

  return (
    <div style={{ marginTop: 28 }}>
      <h3 style={{ fontSize: 13, textTransform: "uppercase", color: "var(--text-dim)" }}>Portfolio registry</h3>
      <p className="muted" style={{ fontSize: 12 }}>
        The sites and assets this tenant plans for. Telemetry, forecasts and decisions all key off these — copy an
        asset's id into your data files' <code>asset_id</code> column. Retiring an asset takes it out of planning and
        keeps its history. Requires <code>manage:assets</code> (Tenant Admin or Portfolio Manager).
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div className="row" style={{ gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <form onSubmit={submit(() => addSite.mutate())} className="card" style={{ minWidth: 260 }}>
          <h3>Add site</h3>
          <div className="field"><label>Name</label><input value={siteName} onChange={(e) => setSiteName(e.target.value)} required style={{ width: "100%" }} /></div>
          <div className="row" style={{ gap: 8 }}>
            <div className="field"><label>Latitude</label><input type="number" step="any" value={lat} onChange={(e) => setLat(e.target.value)} style={{ width: 100 }} /></div>
            <div className="field"><label>Longitude</label><input type="number" step="any" value={lon} onChange={(e) => setLon(e.target.value)} style={{ width: 100 }} /></div>
          </div>
          <button type="submit" disabled={addSite.isPending}>Add site</button>
        </form>

        <form onSubmit={submit(() => addAsset.mutate())} className="card" style={{ minWidth: 300 }}>
          <h3>Add asset</h3>
          <div className="field"><label>Name</label><input value={assetName} onChange={(e) => setAssetName(e.target.value)} required style={{ width: "100%" }} /></div>
          <div className="field">
            <label>Site</label>
            <select value={siteId || sites?.[0]?.id || ""} onChange={(e) => setSiteId(e.target.value)} style={{ width: "100%" }} required>
              {(sites || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <div className="field">
              <label>Type</label>
              <select value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{t}</option>)}</select>
            </div>
            <div className="field"><label>Rated kW</label><input type="number" min={0} step="any" value={capacity} onChange={(e) => setCapacity(e.target.value)} required style={{ width: 100 }} /></div>
            {type === "battery" && (
              <div className="field"><label>Energy kWh</label><input type="number" min={0} step="any" value={energy} onChange={(e) => setEnergy(e.target.value)} required style={{ width: 100 }} /></div>
            )}
          </div>
          <button type="submit" disabled={addAsset.isPending || !(sites || []).length}>Add asset</button>
          {!(sites || []).length && <div className="muted" style={{ fontSize: 11 }}>Add a site first.</div>}
        </form>
      </div>

      <div className="table-scroll" style={{ marginTop: 12 }}>
        <table>
          <thead><tr><th>Asset</th><th>Type</th><th>Site</th><th>Rated kW</th><th>Battery</th><th>Id</th><th></th></tr></thead>
          <tbody>
            {(assets || []).map((a) => (
              <tr key={a.id} style={a.retired ? { opacity: 0.5 } : undefined}>
                <td>{a.name} {a.retired && <Badge text="retired" />}</td>
                <td>{a.asset_type}</td>
                <td>{a.site_name}</td>
                <td>{a.rated_capacity_kw}</td>
                <td>{a.battery ? `${a.battery.energy_capacity_kwh} kWh, SoC ${a.battery.soc_min_pct}–${a.battery.soc_max_pct}%` : "—"}</td>
                <td className="mono" style={{ fontSize: 11 }}>{a.id}</td>
                <td>
                  <button className="secondary" disabled={retire.isPending}
                          onClick={() => {
                            if (!a.retired && !window.confirm(`Retire ${a.name}? It drops out of planning; its history is kept.`)) return;
                            retire.mutate({ id: a.id, retired: !a.retired });
                          }}>
                    {a.retired ? "Restore" : "Retire"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
