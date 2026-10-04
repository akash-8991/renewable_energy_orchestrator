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
      <h2 className="page-title">Simulation Lab</h2>
      <p className="page-intro">
        Inject live shock scenarios into the simulated portfolio. Changes apply within one simulator tick
        (about 10 seconds); no restart needed.
      </p>

      <div className="card-grid uniform short">
        <div className="card">
          <h3>Shock scenario</h3>
          <div className="card-body">
            {([
              ["cloud_cover", "Cloud cover (solar loss)", 0, 1, 0.05, (v: number) => `${(v * 100).toFixed(0)}%`],
              ["wind_surge", "Wind surge (speed multiplier)", 0, 3, 0.1, (v: number) => `×${v.toFixed(1)}`],
              ["price_spike", "Price spike (multiplier)", 0, 5, 0.1, (v: number) => `×${v.toFixed(1)}`],
              ["demand_shock", "Demand shock (multiplier)", 0, 5, 0.1, (v: number) => `×${v.toFixed(1)}`],
            ] as const).map(([key, label, min, max, step, fmt]) => (
              <div className="field slider" key={key}>
                <div className="slider-label"><span>{label}</span><b>{fmt(local[key])}</b></div>
                <input type="range" min={min} max={max} step={step} value={local[key]} onChange={(e) => setLocal({ ...local, [key]: +e.target.value })} />
              </div>
            ))}
            <div className="field">
              <label>Battery outage — asset ID (blank = none)</label>
              <input value={local.battery_outage_asset} onChange={(e) => setLocal({ ...local, battery_outage_asset: e.target.value })} />
            </div>
            <label className="check">
              <input type="checkbox" checked={local.line_congestion} onChange={(e) => setLocal({ ...local, line_congestion: e.target.checked })} />
              Line congestion (clamps grid export to 30%)
            </label>
          </div>
          <div className="card-footer">
            <button onClick={() => apply.mutate(local)} disabled={apply.isPending}>Apply scenario</button>
            <button className="secondary" onClick={() => reset.mutate()} disabled={reset.isPending}>Reset to baseline</button>
          </div>
        </div>
      </div>
    </div>
  );
}
