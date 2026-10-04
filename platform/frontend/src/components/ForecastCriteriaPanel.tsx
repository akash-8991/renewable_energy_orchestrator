import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import Badge from "./Badge";

interface Values {
  model_mode: "physics" | "ml" | "auto"; band_scale: number; min_training_hours: number;
  min_improvement_pct: number; retrain_hours: number;
}
interface ModelRow {
  asset_id: string; asset_name: string | null; variable: string; status: string; n_samples: number;
  trained_at: string | null; mae_ml: number | null; mae_physics: number | null; improvement_pct: number | null;
  holdout_hours: number | null; applied: boolean;
}
interface CriteriaView {
  status: "proposed" | "accepted" | "custom"; in_force: Values; proposal: Values;
  decided_by: string | null; decided_at: string | null; note: string; models: ModelRow[];
}

const MODE_HELP: Record<string, string> = {
  physics: "Physics-based baseline only.",
  ml: "Trained ML model for every asset that has one; baseline for the rest.",
  auto: "Trained ML model only where it beats the baseline by the margin below; baseline elsewhere.",
};

export default function ForecastCriteriaPanel() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Values | null>(null);

  const { data } = useQuery<CriteriaView>({
    queryKey: ["forecast-criteria"],
    queryFn: async () => (await api.get("/forecasting/criteria")).data,
  });
  useEffect(() => {
    if (data && !editing) setForm(data.in_force);
  }, [data, editing]);

  const done = (view: CriteriaView) => {
    qc.setQueryData(["forecast-criteria"], view);
    setError(null);
    setEditing(false);
  };
  const onError = (err: any) => setError(err?.response?.data?.detail || "Request failed");
  const accept = useMutation({ mutationFn: async () => (await api.post("/forecasting/criteria/accept")).data, onSuccess: done, onError });
  const save = useMutation({ mutationFn: async (v: Values) => (await api.put("/forecasting/criteria", v)).data, onSuccess: done, onError });
  const retrain = useMutation({ mutationFn: async () => (await api.post("/forecasting/retrain")).data, onSuccess: done, onError });

  if (!data || !form) return null;
  const set = <K extends keyof Values>(k: K, v: Values[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const inForce = data.in_force;

  return (
    <div className="card">
      <h3>Forecast criteria</h3>
      <p className="card-help" style={{ marginBottom: 10 }}>
        Forecasts are a physics-based baseline, and the platform also trains an ML model per asset on the data it has
        ingested. <b>Accept</b> the proposed criteria or <b>set your own</b>; until you do, the physics baseline is used.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div className="row" style={{ marginBottom: 10, gap: 8 }}>
        <Badge text={data.status} />
        <span className="muted" style={{ fontSize: 13 }}>
          in force: <b>{inForce.model_mode}</b>, band ×{inForce.band_scale}
          {data.status !== "proposed" && data.decided_by && ` · ${data.decided_by}, ${new Date(data.decided_at!).toLocaleDateString()}`}
        </span>
      </div>

      <div className="card-body">
        {!editing ? (
          <>
            {data.models.length > 0 && (
              <table className="compact">
                <thead>
                  <tr><th>Asset</th><th>Error ML / physics</th><th>Better</th><th>Used</th></tr>
                </thead>
                <tbody>
                  {data.models.map((m) => (
                    <tr key={m.asset_id + m.variable} title={`${m.n_samples} hours of history`}>
                      <td>{m.asset_name ?? m.asset_id.slice(0, 8)}<div className="muted" style={{ fontSize: 12 }}>{m.variable}</div></td>
                      <td>{m.status === "trained" ? `${m.mae_ml} / ${m.mae_physics}` : <span className="muted">no history yet</span>}</td>
                      <td style={{ color: (m.improvement_pct ?? 0) > 0 ? "var(--green)" : undefined, fontWeight: 600 }}>
                        {m.improvement_pct != null ? `${m.improvement_pct}%` : "—"}
                      </td>
                      <td>{m.applied ? <Badge text="ML" /> : <span className="muted">physics</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="field-hint" style={{ marginTop: 10 }}>
              Error is the mean absolute error on the most recent held-out hours (kW; GBP/MWh for price). A model needs at
              least {inForce.min_training_hours} hours of ingested history.
            </p>
          </>
        ) : (
          <div>
            <div className="field">
              <label>Forecast model</label>
              <select value={form.model_mode} onChange={(e) => set("model_mode", e.target.value as Values["model_mode"])}>
                <option value="physics">physics baseline</option>
                <option value="auto">auto (ML where it beats physics)</option>
                <option value="ml">ML wherever available</option>
              </select>
              <div className="field-hint">{MODE_HELP[form.model_mode]}</div>
            </div>
            <div className="field-row">
              <div className="field">
                <label>Uncertainty band ×</label>
                <input type="number" min={0.25} max={4} step={0.05} value={form.band_scale} onChange={(e) => set("band_scale", Number(e.target.value))} />
                <div className="field-hint">wider = more conservative</div>
              </div>
              <div className="field">
                <label>Min history (hours)</label>
                <input type="number" min={48} max={8760} value={form.min_training_hours} onChange={(e) => set("min_training_hours", Number(e.target.value))} />
              </div>
            </div>
            <div className="field-row">
              <div className="field">
                <label>Min improvement (%)</label>
                <input type="number" min={0} max={100} step={0.5} value={form.min_improvement_pct} onChange={(e) => set("min_improvement_pct", Number(e.target.value))} />
                <div className="field-hint">auto mode only</div>
              </div>
              <div className="field">
                <label>Retrain every (hours)</label>
                <input type="number" min={1} max={720} value={form.retrain_hours} onChange={(e) => set("retrain_hours", Number(e.target.value))} />
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="card-footer">
        {!editing ? (
          <>
            <button onClick={() => accept.mutate()} disabled={accept.isPending}
                    title={`Adopts: ${data.proposal.model_mode} mode, band ×${data.proposal.band_scale}, min ${data.proposal.min_training_hours}h history, retrain every ${data.proposal.retrain_hours}h`}>Accept proposed</button>
            <button className="secondary" onClick={() => setEditing(true)}>Set criteria…</button>
            <button className="secondary" onClick={() => retrain.mutate()} disabled={retrain.isPending}>
              {retrain.isPending ? "Retraining…" : "Retrain now"}
            </button>
          </>
        ) : (
          <>
            <button onClick={() => save.mutate(form)} disabled={save.isPending}>Save criteria</button>
            <button className="secondary" onClick={() => { setEditing(false); setForm(data.in_force); }}>Cancel</button>
          </>
        )}
      </div>
    </div>
  );
}
