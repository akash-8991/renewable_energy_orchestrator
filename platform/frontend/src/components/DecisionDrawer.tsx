import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import Badge from "./Badge";

interface DecisionDetail {
  id: string; decision_cycle_id: string; version: number; trigger: string; status: string;
  autonomy_mode: string; confidence: number; binding_constraints: string[]; risk_flags: any[];
  created_at: string; expires_at: string | null;
  plan: any; alternatives: any[]; reasoning: any; trusted_snapshot_ref: string | null; forecast_bundle_ref: string | null;
}

interface ScenarioRun {
  scenario_name: string; solver_status: string; objective_value: number; delta_vs_baseline: number | null;
  total_import_kwh: number; total_export_kwh: number; total_curtailment_kwh: number; total_shed_kwh: number;
  binding_constraints: string[]; confidence: number;
}

function fmtKwh(v: number) {
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function ScenarioComparisonTab({ decisionId }: { decisionId: string }) {
  const { data, isLoading } = useQuery<ScenarioRun[]>({
    queryKey: ["scenario-runs", decisionId],
    queryFn: async () => (await api.get(`/decisions/${decisionId}/scenario-runs`)).data,
  });

  if (isLoading) return <div className="empty-state">Loading scenario comparison...</div>;
  if (!data || data.length === 0)
    return <div className="muted">No scenario simulation for this decision (only computed when the base solve succeeds).</div>;

  const baseline = data.find((r) => r.scenario_name === "BASELINE");

  return (
    <div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Each row is a full 24h re-solve of this same decision cycle under one named variation — a forward
        simulation, not something that has actually happened. Δ objective is relative to BASELINE (lower is better).
      </p>
      <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Scenario</th><th>Status</th><th>Δ Objective</th><th>Import</th><th>Export</th><th>Curtailed</th><th>Shed</th><th>Binding</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.scenario_name}>
              <td><Badge text={r.scenario_name} /></td>
              <td><Badge text={r.solver_status} /></td>
              <td style={{ color: r.delta_vs_baseline == null ? undefined : r.delta_vs_baseline > 0 ? "var(--red)" : "var(--green)" }}>
                {r.delta_vs_baseline == null ? "—" : (r.delta_vs_baseline > 0 ? "+" : "") + r.delta_vs_baseline.toFixed(0)}
              </td>
              <td>{fmtKwh(r.total_import_kwh)} kWh</td>
              <td>{fmtKwh(r.total_export_kwh)} kWh</td>
              <td>{r.total_curtailment_kwh > 0 ? <span style={{ color: "var(--amber)" }}>{fmtKwh(r.total_curtailment_kwh)} kWh</span> : "—"}</td>
              <td>{r.total_shed_kwh > 0 ? <span style={{ color: "var(--amber)" }}>{fmtKwh(r.total_shed_kwh)} kWh</span> : "—"}</td>
              <td className="muted" style={{ fontSize: 11 }}>{r.binding_constraints.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {baseline && <p className="muted" style={{ fontSize: 11 }}>Baseline objective: {baseline.objective_value.toFixed(0)}</p>}
    </div>
  );
}

export default function DecisionDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, isLoading } = useQuery<DecisionDetail>({
    queryKey: ["decision", id],
    queryFn: async () => (await api.get(`/decisions/${id}`)).data,
  });
  const [tab, setTab] = useState<"plan" | "reasoning" | "explanation" | "scenarios">("explanation");

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row-between">
        <h3 style={{ textTransform: "none", fontSize: 14 }}>Decision {id.slice(0, 8)}</h3>
        <button className="ghost" onClick={onClose}>close ✕</button>
      </div>
      {isLoading || !data ? (
        <div className="empty-state">Loading...</div>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
            <Badge text={data.status} />
            <Badge text={data.autonomy_mode} />
            <span className="muted">confidence {(data.confidence * 100).toFixed(0)}%</span>
            <span className="muted">cycle {data.decision_cycle_id}</span>
          </div>
          {data.binding_constraints.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <span className="muted">binding: </span>
              {data.binding_constraints.map((c) => (
                <Badge key={c} text={c} />
              ))}
            </div>
          )}
          {data.risk_flags.length > 0 && (
            <div className="evidence-box">
              {data.risk_flags.map((f, i) => (
                <div key={i} className="finding">⚠ {String(f)}</div>
              ))}
            </div>
          )}
          <div className="tabs">
            <div className={"tab" + (tab === "explanation" ? " active" : "")} onClick={() => setTab("explanation")}>Operator Explanation</div>
            <div className={"tab" + (tab === "reasoning" ? " active" : "")} onClick={() => setTab("reasoning")}>Agent Findings</div>
            <div className={"tab" + (tab === "scenarios" ? " active" : "")} onClick={() => setTab("scenarios")}>Scenario Comparison</div>
            <div className={"tab" + (tab === "plan" ? " active" : "")} onClick={() => setTab("plan")}>Raw Plan</div>
          </div>
          {tab === "explanation" && (
            <div>
              {data.reasoning?.explanation ? (
                <div className="evidence-box">
                  {Object.entries(data.reasoning.explanation)
                    .filter(([k]) => !["decision_id"].includes(k))
                    .map(([k, v]) => (
                      <div key={k} className="finding">
                        <strong>{k.replace(/_/g, " ")}:</strong> {String(v)}
                      </div>
                    ))}
                </div>
              ) : (
                <div className="muted">No explanation generated yet for this decision.</div>
              )}
              {data.alternatives?.length > 0 && (
                <div className="evidence-box">
                  <strong>Alternatives considered</strong>
                  {data.alternatives.map((alt, i) => (
                    <div key={i} className="finding">
                      {alt.label}: {alt.status}, Δobjective {alt.delta_objective}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {tab === "reasoning" && (
            <div>
              {(data.reasoning?.agent_findings || []).map((f: any, i: number) => (
                <div key={i} className="evidence-box">
                  <div className="row-between">
                    <strong>{f.agent}</strong>
                    <Badge text={f.status} />
                  </div>
                  {(f.findings || []).map((finding: any, j: number) => (
                    <div key={j} className="finding">
                      <Badge text={finding.severity} /> {finding.finding}
                    </div>
                  ))}
                  {f.findings?.length === 0 && <div className="muted" style={{ fontSize: 12 }}>no findings raised</div>}
                </div>
              ))}
              {data.reasoning?.governance && (
                <div className="evidence-box">
                  <strong>Governance</strong>
                  <div className="finding">
                    risk_tier: <Badge text={data.reasoning.governance.risk_tier} /> · requires_approval: {String(data.reasoning.governance.requires_human_approval)}
                  </div>
                  <div className="finding muted">{data.reasoning.governance.rationale}</div>
                </div>
              )}
            </div>
          )}
          {tab === "scenarios" && <ScenarioComparisonTab decisionId={id} />}
          {tab === "plan" && (
            <pre className="mono" style={{ background: "var(--bg-code)", border: "1px solid var(--border)", padding: 12, borderRadius: 8, overflow: "auto", maxHeight: 400 }}>
              {JSON.stringify(data.plan, null, 2)}
            </pre>
          )}
        </>
      )}
    </div>
  );
}
