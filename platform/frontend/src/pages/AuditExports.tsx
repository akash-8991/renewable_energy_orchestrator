import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface AuditEvent { id: string; event_type: string; actor_label: string; payload: any; hash: string; created_at: string }
interface AuditChain { events: AuditEvent[]; chain_valid: boolean; broken_at_event_id: string | null }
interface ExportJob { id: string; status: string; row_count: number | null; checksum_sha256: string | null; download_url: string | null }

export default function AuditExports() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"audit" | "exports">("audit");
  const [polling, setPolling] = useState<string | null>(null);

  const { data: chain } = useQuery<AuditChain>({
    queryKey: ["audit-events"],
    queryFn: async () => (await api.get("/audit/events", { params: { limit: 100 } })).data,
    refetchInterval: 15000,
  });

  const { data: activeExport } = useQuery<ExportJob>({
    queryKey: ["export", polling],
    queryFn: async () => (await api.get(`/exports/${polling}`)).data,
    enabled: !!polling,
    refetchInterval: (query) => (query.state.data?.status === "complete" || query.state.data?.status === "failed" ? false : 1500),
  });

  const requestExport = useMutation({
    mutationFn: async () => (await api.post("/exports", { limit: 500 })).data as ExportJob,
    onSuccess: (job) => setPolling(job.id),
  });

  return (
    <div>
      <h2 className="page-title">Audit & Exports</h2>
      <p className="page-intro">Verify the tamper-evident audit chain and export governed evidence.</p>
      <div className="tabs">
        <div className={"tab" + (tab === "audit" ? " active" : "")} onClick={() => setTab("audit")}>Audit Chain</div>
        <div className={"tab" + (tab === "exports" ? " active" : "")} onClick={() => setTab("exports")}>Governed Export</div>
      </div>

      {tab === "audit" && (
        <div>
          <div className="row" style={{ marginBottom: 12 }}>
            <span className="muted">Hash-chain integrity:</span>
            {chain ? <Badge text={chain.chain_valid ? "valid" : "BROKEN"} /> : "—"}
          </div>
          <div className="table-scroll">
          <table>
            <thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Hash</th></tr></thead>
            <tbody>
              {(chain?.events || []).slice().reverse().map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.created_at).toLocaleString()}</td>
                  <td>{e.event_type}</td>
                  <td>{e.actor_label}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{e.hash.slice(0, 12)}...</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {tab === "exports" && (
        <div className="card-grid"><div className="card">
          <h3>Governed export</h3>
          <p className="card-help">
            Generates a governed Excel workbook for the last 500 decisions, with Decisions, Signals, Reasoning,
            Approvals, Acknowledgements and Export Metadata sheets. Requires the Auditor/DPO role.
          </p>
          {activeExport && (
            <div className="card-body">
              <div className="row" style={{ marginBottom: 10 }}>
                <Badge text={activeExport.status} />
                {activeExport.row_count !== null && <span className="muted">{activeExport.row_count} rows</span>}
              </div>
              {activeExport.checksum_sha256 && (
                <div className="field">
                  <label>SHA-256 checksum</label>
                  <div className="mono break-all">{activeExport.checksum_sha256}</div>
                </div>
              )}
            </div>
          )}
          <div className="card-footer">
            <button onClick={() => requestExport.mutate()} disabled={requestExport.isPending || (!!polling && activeExport?.status !== "complete" && activeExport?.status !== "failed")}>
              Generate export
            </button>
            {activeExport?.download_url && (
              <a href={activeExport.download_url}><button className="secondary">Download .xlsx</button></a>
            )}
          </div>
        </div></div>
      )}
    </div>
  );
}
