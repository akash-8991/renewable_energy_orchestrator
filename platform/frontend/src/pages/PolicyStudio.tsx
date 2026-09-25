import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface AutonomyPolicy {
  id: string; scope: string; mode: string; max_action_risk: string; safety_case_ref: string | null; effective_from: string;
}

interface ObjectivePolicy {
  id: string; version: number; weights: Record<string, number>; carbon_price_per_tonne: number;
  risk_aversion: number; combination_method: string; approved_by: string | null; created_at: string;
}

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
      <h2 style={{ fontSize: 15 }}>Policy Studio</h2>
      <p className="muted">Set the portfolio-wide autonomy mode, and the emergency stop.</p>
      {error && <div className="error-banner">{error}</div>}

      <div className="card" style={{ marginBottom: 16, maxWidth: 480 }}>
        <h3>Current portfolio-wide policy</h3>
        {current ? (
          <div>
            <Badge text={current.mode} /> <span className="muted">max risk: {current.max_action_risk}</span>
            {current.safety_case_ref && <div className="muted" style={{ marginTop: 6 }}>safety case: {current.safety_case_ref}</div>}
          </div>
        ) : (
          <div className="muted">No policy configured — defaults to the conservative OBSERVE mode.</div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16, maxWidth: 480 }}>
        <h3>Set new policy</h3>
        <div className="field">
          <label>Mode</label>
          <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ width: "100%" }}>
            {MODES.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        {mode === "AUTONOMOUS_BOUNDED" && (
          <>
            <div className="field">
              <label>Max action risk (ceiling for unattended dispatch)</label>
              <select value={maxActionRisk} onChange={(e) => setMaxActionRisk(e.target.value)} style={{ width: "100%" }}>
                {RISK_LEVELS.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                Only actions classified at or below this risk dispatch autonomously; anything above still
                falls back to requiring human approval even in this mode.
              </p>
            </div>
            <div className="field">
              <label>Safety case reference (required — doc 05 §7)</label>
              <input value={safetyCaseRef} onChange={(e) => setSafetyCaseRef(e.target.value)} placeholder="e.g. SC-2026-001" style={{ width: "100%" }} />
            </div>
          </>
        )}
        <button onClick={() => setPolicy.mutate()} disabled={setPolicy.isPending}>Apply</button>
      </div>

      <div className="card" style={{ marginBottom: 16, maxWidth: 480 }}>
        <h3>Optimality criteria (objective policy)</h3>
        <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
          What the optimizer trades off against what — cost, degradation, carbon, curtailment, reliability —
          plus how conservative it plans against forecast uncertainty. Requires the portfolio_manager role.
          {objectivePolicy && <> Current version: {objectivePolicy.version}, set by {objectivePolicy.approved_by || "seed"}.</>}
        </p>
        {WEIGHT_KEYS.map((k) => (
          <div className="field" key={k}>
            <label>{k} weight ({(effectiveWeights[k] ?? 0).toFixed(2)})</label>
            <input
              type="range" min={0} max={1} step={0.05} value={effectiveWeights[k] ?? 0}
              onChange={(e) => setWeights({ ...effectiveWeights, [k]: +e.target.value })}
              style={{ width: "100%" }}
            />
          </div>
        ))}
        <div className="field">
          <label>Carbon price (£/tonne CO2e): {carbonPrice}</label>
          <input type="range" min={0} max={300} step={5} value={carbonPrice} onChange={(e) => setCarbonPrice(+e.target.value)} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Risk aversion (0=plan to median forecast, 1=plan to worst-case tail): {riskAversion.toFixed(2)}</label>
          <input type="range" min={0} max={1} step={0.05} value={riskAversion} onChange={(e) => setRiskAversion(+e.target.value)} style={{ width: "100%" }} />
        </div>
        <button onClick={() => setObjectivePolicy.mutate()} disabled={setObjectivePolicy.isPending}>Apply (creates a new version)</button>
      </div>

      <div className="card" style={{ maxWidth: 480, borderColor: estopActive ? "var(--red)" : undefined }}>
        <h3>Emergency stop</h3>
        <p className="muted" style={{ fontSize: 12 }}>
          Disables all new OT command dispatch for this tenant immediately. Monitoring continues unaffected.
        </p>
        <button className={estopActive ? "secondary" : "danger"} onClick={() => estop.mutate(!estopActive)} disabled={estop.isPending}>
          {estopActive ? "Clear e-stop" : "Trigger e-stop"}
        </button>
      </div>
    </div>
  );
}
