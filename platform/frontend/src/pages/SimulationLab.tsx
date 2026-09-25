import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";

interface ScenarioState {
  cloud_cover: number; wind_surge: number; price_spike: number;
  battery_outage_asset: string; line_congestion: boolean; demand_shock: number;
}

const DEFAULT: ScenarioState = { cloud_cover: 0, wind_surge: 1, price_spike: 1, battery_outage_asset: "", line_congestion: false, demand_shock: 1 };

export default function SimulationLab() {
  const qc = useQueryClient();
  const [local, setLocal] = useState<ScenarioState>(DEFAULT);

  const { data } = useQuery<ScenarioState>({
    queryKey: ["scenario"],
    queryFn: async () => (await api.get("/simulation/scenario")).data,
  });

  useEffect(() => {
    if (data) setLocal(data);
  }, [data]);

  const apply = useMutation({
    mutationFn: async (s: ScenarioState) => (await api.put("/simulation/scenario", s)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scenario"] }),
  });
  const reset = useMutation({
    mutationFn: async () => (await api.post("/simulation/scenario/reset")).data,
    onSuccess: (d) => {
      setLocal(d);
      qc.invalidateQueries({ queryKey: ["scenario"] });
    },
  });

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Simulation Lab</h2>
      <p className="muted">
        Drives the edge-simulator's live shock scenarios (doc 08 §4 demo steps 2-6) — changes apply within one
        simulator tick (~10s), no restart needed.
      </p>

      <div className="card" style={{ maxWidth: 520 }}>
        <div className="field">
          <label>Cloud cover ({(local.cloud_cover * 100).toFixed(0)}% solar loss)</label>
          <input type="range" min={0} max={1} step={0.05} value={local.cloud_cover} onChange={(e) => setLocal({ ...local, cloud_cover: +e.target.value })} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Wind surge (×{local.wind_surge.toFixed(1)} speed)</label>
          <input type="range" min={0} max={3} step={0.1} value={local.wind_surge} onChange={(e) => setLocal({ ...local, wind_surge: +e.target.value })} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Price spike (×{local.price_spike.toFixed(1)})</label>
          <input type="range" min={0} max={5} step={0.1} value={local.price_spike} onChange={(e) => setLocal({ ...local, price_spike: +e.target.value })} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Demand shock (×{local.demand_shock.toFixed(1)})</label>
          <input type="range" min={0} max={5} step={0.1} value={local.demand_shock} onChange={(e) => setLocal({ ...local, demand_shock: +e.target.value })} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Battery outage — asset ID (blank = none)</label>
          <input value={local.battery_outage_asset} onChange={(e) => setLocal({ ...local, battery_outage_asset: e.target.value })} style={{ width: "100%" }} />
        </div>
        <div className="field row">
          <input type="checkbox" checked={local.line_congestion} onChange={(e) => setLocal({ ...local, line_congestion: e.target.checked })} id="congestion" />
          <label htmlFor="congestion" style={{ margin: 0 }}>Line congestion (clamps grid export to 30%)</label>
        </div>
        <div className="row">
          <button onClick={() => apply.mutate(local)} disabled={apply.isPending}>Apply scenario</button>
          <button className="secondary" onClick={() => reset.mutate()} disabled={reset.isPending}>Reset to baseline</button>
        </div>
      </div>
    </div>
  );
}
