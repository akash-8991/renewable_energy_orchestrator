import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";
import ForecastCriteriaPanel from "../components/ForecastCriteriaPanel";

interface AutonomyPolicy {
  id: string; scope: string; mode: string; max_action_risk: string; safety_case_ref: string | null; effective_from: string;
}

interface ObjectivePolicy {
  id: string; version: number; weights: Record<string, number>; carbon_price_per_tonne: number;
  risk_aversion: number; combination_method: string; approved_by: string | null; created_at: string;
}

const MODE_DESCRIPTIONS: Record<string, string> = {
  OBSERVE: "Decisions are planned and recorded, but nothing is sent for approval or dispatched.",
  RECOMMEND: "Recommended actions are recorded for operators to review; none are sent for approval or dispatched.",
  APPROVAL_REQUIRED: "Every dispatchable action waits for a human approval before it reaches the OT gateway.",
  AUTONOMOUS_BOUNDED: "Low-risk actions within the ceiling dispatch automatically; anything above still needs approval.",
};
const MODES = ["OBSERVE", "RECOMMEND", "APPROVAL_REQUIRED", "AUTONOMOUS_BOUNDED"];
const RISK_LEVELS = ["low", "medium", "high"];
const WEIGHT_KEYS = ["cost", "degradation", "carbon", "curtailment", "reliability"] as const;

export default function PolicyStudio() {
  const qc = useQueryClient();
  const [mode, setMode] = useState("OBSERVE");
  const [maxActionRisk, setMaxActionRisk] = useState("low");
  const [safetyCaseRef, setSafetyCaseRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [estopActive, setEstopActive] = useState(false);

  const { data } = useQuery<AutonomyPolicy[]>({
    queryKey: ["autonomy-policy"],
    queryFn: async () => (await api.get("/governance/autonomy-policy")).data,
  });

  const setPolicy = useMutation({
    mutationFn: async () =>
      (await api.put("/governance/autonomy-policy", {
        scope: "portfolio", mode, max_action_risk: maxActionRisk, safety_case_ref: safetyCaseRef || undefined,
      })).data,
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["autonomy-policy"] });
    },
    onError: (err: any) => setError(err?.response?.data?.detail || "Failed to set policy"),
  });

  const estop = useMutation({
    mutationFn: async (active: boolean) => (await api.post("/governance/e-stop", { active, reason: "triggered from Policy Studio" })).data,
    onSuccess: (_, active) => setEstopActive(active),
  });

  const current = data?.[0];

  const { data: objectivePolicy } = useQuery<ObjectivePolicy>({
    queryKey: ["objective-policy"],
    queryFn: async () => (await api.get("/governance/objective-policy")).data,
  });
  const [weights, setWeights] = useState<Record<string, number> | null>(null);
  const [carbonPrice, setCarbonPrice] = useState(80);
  const [riskAversion, setRiskAversion] = useState(0.2);
  const effectiveWeights = weights ?? objectivePolicy?.weights ?? { cost: 0.35, degradation: 0.1, carbon: 0.15, curtailment: 0.15, reliability: 0.1 };

  useEffect(() => {
    if (objectivePolicy) {
      setCarbonPrice(objectivePolicy.carbon_price_per_tonne);
      setRiskAversion(objectivePolicy.risk_aversion);
    }
  }, [objectivePolicy?.id]);

  const setObjectivePolicy = useMutation({
    mutationFn: async () =>
      (await api.put("/governance/objective-policy", {
        weights: effectiveWeights, carbon_price_per_tonne: carbonPrice, risk_aversion: riskAversion,
      })).data,
    onSuccess: () => {
      setError(null);
      setWeights(null);
      qc.invalidateQueries({ queryKey: ["objective-policy"] });
    },
    onError: (err: any) => setError(err?.response?.data?.detail || "Failed to set objective policy — requires the portfolio_manager role"),
  });

  return (
    <div>
      <h2 className="page-title">Policy Studio</h2>
      <p className="page-intro">Set the portfolio-wide autonomy mode, how the optimizer trades off its objectives, the forecast criteria, and the emergency stop.</p>
      {error && <div className="error-banner">{error}</div>}

      <div className="card-grid uniform">
        <div className="card">
          <h3>Current portfolio-wide policy</h3>
          <div className="card-body">
            {current ? (
              <>
                <div className="row" style={{ marginBottom: 12 }}>
                  <Badge text={current.mode} />
                  <span className="muted">max action risk: {current.max_action_risk}</span>
                </div>
                <p className="card-help">{MODE_DESCRIPTIONS[current.mode] ?? ""}</p>
                {current.safety_case_ref && <div className="field-hint" style={{ fontSize: 13 }}>Safety case: <span className="mono">{current.safety_case_ref}</span></div>}
                <div className="field-hint" style={{ fontSize: 13 }}>In force since {new Date(current.effective_from).toLocaleString()}</div>
              </>
            ) : (
              <>
                <div className="row" style={{ marginBottom: 12 }}><Badge text="OBSERVE" /><span className="muted">default</span></div>
                <p className="card-help">No policy configured — the platform defaults to the conservative OBSERVE mode. {MODE_DESCRIPTIONS.OBSERVE}</p>
              </>
            )}
          </div>
        </div>

        <div className="card">
          <h3>Set new policy</h3>
          <div className="card-body">
            <div className="field">
              <label>Mode</label>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                {MODES.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              <div className="field-hint">{MODE_DESCRIPTIONS[mode]}</div>
            </div>
            {mode === "AUTONOMOUS_BOUNDED" && (
              <>
                <div className="field">
                  <label>Max action risk (ceiling for unattended dispatch)</label>
                  <select value={maxActionRisk} onChange={(e) => setMaxActionRisk(e.target.value)}>
                    {RISK_LEVELS.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                  <div className="field-hint">
                    Only actions classified at or below this risk dispatch autonomously; anything above still
                    needs human approval.
                  </div>
                </div>
                <div className="field">
                  <label>Safety case reference (required)</label>
                  <input value={safetyCaseRef} onChange={(e) => setSafetyCaseRef(e.target.value)} placeholder="e.g. SC-2026-001" />
                </div>
              </>
            )}
          </div>
          <div className="card-footer">
            <button onClick={() => setPolicy.mutate()} disabled={setPolicy.isPending}>Apply</button>
          </div>
        </div>

        <div className="card">
          <h3>Optimality criteria</h3>
          <p className="card-help" style={{ marginBottom: 8 }}>
            What the optimizer trades off, and how conservatively it plans. Portfolio manager role required.
            {objectivePolicy && <> Version {objectivePolicy.version}, set by {!objectivePolicy.approved_by || objectivePolicy.approved_by === "seed-script" ? "system default" : objectivePolicy.approved_by}.</>}
          </p>
          <div className="card-body">
            <div className="slider-grid">
              {WEIGHT_KEYS.map((k) => (
                <div className="field slider" key={k}>
                  <div className="slider-label"><span>{k[0].toUpperCase() + k.slice(1)}</span><b>{(effectiveWeights[k] ?? 0).toFixed(2)}</b></div>
                  <input type="range" min={0} max={1} step={0.05} value={effectiveWeights[k] ?? 0}
                         onChange={(e) => setWeights({ ...effectiveWeights, [k]: +e.target.value })} />
                </div>
              ))}
              <div className="field slider">
                <div className="slider-label"><span>Carbon price (£/t)</span><b>{carbonPrice}</b></div>
                <input type="range" min={0} max={300} step={5} value={carbonPrice} onChange={(e) => setCarbonPrice(+e.target.value)} />
              </div>
              <div className="field slider">
                <div className="slider-label"><span>Risk aversion</span><b>{riskAversion.toFixed(2)}</b></div>
                <input type="range" min={0} max={1} step={0.05} value={riskAversion} onChange={(e) => setRiskAversion(+e.target.value)} />
              </div>
            </div>
            <div className="field-hint">Weights trade the objectives off against each other. Risk aversion: 0 plans to the median forecast, 1 to the worst-case tail. Carbon price in £ per tonne CO₂e.</div>
          </div>
          <div className="card-footer">
            <button onClick={() => setObjectivePolicy.mutate()} disabled={setObjectivePolicy.isPending}>Apply (creates a new version)</button>
          </div>
        </div>

        <ForecastCriteriaPanel />

        <div className="card" style={{ borderColor: estopActive ? "var(--red)" : undefined }}>
          <h3>Emergency stop</h3>
          <div className="card-body">
            <div className="row" style={{ marginBottom: 12 }}>
              <Badge text={estopActive ? "e-stop active" : "inactive"} />
            </div>
            <p className="card-help">
              Disables all new OT command dispatch for this tenant immediately. Monitoring continues unaffected.
              {estopActive && " Dispatch stays disabled until the e-stop is cleared."}
            </p>
          </div>
          <div className="card-footer">
            <button className={estopActive ? "secondary" : "danger"} onClick={() => estop.mutate(!estopActive)} disabled={estop.isPending}>
              {estopActive ? "Clear e-stop" : "Trigger e-stop"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
