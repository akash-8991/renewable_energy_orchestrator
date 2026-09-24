import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface AutonomyPolicy {
  id: string; scope: string; mode: string; max_action_risk: string; safety_case_ref: string | null; effective_from: string;
}

const MODES = ["OBSERVE", "RECOMMEND", "APPROVAL_REQUIRED", "AUTONOMOUS_BOUNDED"];

export default function PolicyStudio() {
  const qc = useQueryClient();
  const [mode, setMode] = useState("OBSERVE");
  const [safetyCaseRef, setSafetyCaseRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [estopActive, setEstopActive] = useState(false);

  const { data } = useQuery<AutonomyPolicy[]>({
    queryKey: ["autonomy-policy"],
    queryFn: async () => (await api.get("/governance/autonomy-policy")).data,
  });

  const setPolicy = useMutation({
    mutationFn: async () =>
      (await api.put("/governance/autonomy-policy", { scope: "portfolio", mode, safety_case_ref: safetyCaseRef || undefined })).data,
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
          <div className="field">
            <label>Safety case reference (required — doc 05 §7)</label>
            <input value={safetyCaseRef} onChange={(e) => setSafetyCaseRef(e.target.value)} placeholder="e.g. SC-2026-001" style={{ width: "100%" }} />
          </div>
        )}
        <button onClick={() => setPolicy.mutate()} disabled={setPolicy.isPending}>Apply</button>
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
