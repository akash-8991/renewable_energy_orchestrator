import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface DocumentIntakeRow {
  id: string;
  filename: string;
  document_type: string;
  summary: string;
  affected_asset_refs: string[];
  effective_from: string | null;
  effective_to: string | null;
  severity: string;
  capacity_impact_pct: number | null;
  confidence: number;
  raw_excerpt: string;
  status: string;
  constraint_id: string | null;
  created_at: string;
}

interface PortfolioAsset {
  id: string;
  name: string;
  asset_type: string;
}

interface PortfolioSnapshot {
  sites: { assets: PortfolioAsset[] }[];
}

function toDatetimeLocal(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 16);
}

function ApplyConstraintForm({ doc, assets, onDone }: { doc: DocumentIntakeRow; assets: PortfolioAsset[]; onDone: () => void }) {
  const [assetId, setAssetId] = useState(assets[0]?.id || "");
  const [pct, setPct] = useState(doc.capacity_impact_pct ?? 0);
  const [from, setFrom] = useState(toDatetimeLocal(doc.effective_from));
  const [to, setTo] = useState(toDatetimeLocal(doc.effective_to));
  const [error, setError] = useState<string | null>(null);

  // `assets` comes from a separate, slower-loading /twin/portfolio query — on
  // first mount it's often still [] (asset_id would silently stay ""), and
  // the <select> below would visually show a selection with nothing backing
  // it in state. Sync once real options arrive, without clobbering a choice
  // the user already made.
  useEffect(() => {
    if (assets.length > 0 && !assets.some((a) => a.id === assetId)) {
      setAssetId(assets[0].id);
    }
  }, [assets, assetId]);

  const apply = useMutation({
    mutationFn: async () =>
      (await api.post(`/ingestion/documents/${doc.id}/apply-constraint`, {
        asset_id: assetId,
        max_capacity_pct: pct,
        effective_from: from ? new Date(from).toISOString() : undefined,
        effective_to: to ? new Date(to).toISOString() : undefined,
      })).data,
    onSuccess: () => { setError(null); onDone(); },
    onError: (err: any) => setError(err?.response?.data?.detail || "failed to apply constraint"),
  });

  if (assets.length === 0) {
    return <div className="muted" style={{ fontSize: 12 }}>No solar/wind assets available to apply this to.</div>;
  }

  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
      {error && <div className="error-banner" style={{ marginBottom: 8 }}>{error}</div>}
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label style={{ fontSize: 11 }}>Asset</label>
          <select value={assetId} onChange={(e) => setAssetId(e.target.value)}>
            {assets.map((a) => (
              <option key={a.id} value={a.id}>{a.name} ({a.asset_type})</option>
            ))}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label style={{ fontSize: 11 }}>Max capacity %</label>
          <input type="number" min={0} max={100} value={pct} onChange={(e) => setPct(+e.target.value)} style={{ width: 80 }} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label style={{ fontSize: 11 }}>From</label>
          <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label style={{ fontSize: 11 }}>To</label>
          <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <button onClick={() => apply.mutate()} disabled={apply.isPending || !from || !to || !assetId}>
          Apply as constraint
        </button>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: 6, marginBottom: 0 }}>
        Only wired into the optimizer for solar/wind generation assets — this caps that asset's forecast
        generation to the given % of rated capacity for every step inside the window, from the next decision cycle onward.
      </p>
    </div>
  );
}

const DOCUMENT_SUFFIXES = [".pdf", ".png", ".jpg", ".jpeg", ".docx"];
const TABLE_SUFFIXES = [".csv", ".json", ".xlsx", ".xlsm"];
const ALL_SUFFIXES = [...DOCUMENT_SUFFIXES, ...TABLE_SUFFIXES];

function suffixOf(filename: string): string {
  const i = filename.lastIndexOf(".");
  return i === -1 ? "" : filename.slice(i).toLowerCase();
}

interface FileUploadResult {
  filename: string;
  status: "ok" | "error";
  message: string;
}

interface MappingProposal {
  id: string;
  filename: string;
  file_kind: string;
  confidence: number;
  reasoning: string;
  column_roles: Record<string, string>;
  sample_preview: { headers: string[]; rows: Record<string, any>[] };
  status: string;
  created_at: string;
}

// Column roles as the agent proposed them (generic_table_mapper.py):
// event_time/asset_ref/customer_ref/ignore, or "metric:<name>:<unit>" /
// "customer_field:<name>" — rendered compactly rather than as raw strings.
function roleLabel(role: string): string {
  if (role.startsWith("metric:")) {
    const [, name, unit] = role.split(":");
    return `metric: ${name}${unit ? ` (${unit})` : ""}`;
  }
  if (role.startsWith("customer_field:")) return `field: ${role.split(":")[1]}`;
  return role;
}

function MappingProposalsSection() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const { data: proposals } = useQuery<MappingProposal[]>({
    queryKey: ["mapping-proposals"],
    queryFn: async () => (await api.get("/ingestion/mapping-proposals")).data,
    refetchInterval: 15000,
  });
  const pending = (proposals || []).filter((p) => p.status === "proposed");

  const approve = useMutation({
    mutationFn: async (id: string) => (await api.post(`/ingestion/mapping-proposals/${id}/approve`)).data,
    onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ["mapping-proposals"] }); qc.invalidateQueries({ queryKey: ["operations-status"] }); },
    onError: (err: any) => setError(err?.response?.data?.detail || "failed to approve"),
  });
  const reject = useMutation({
    mutationFn: async (id: string) => (await api.post(`/ingestion/mapping-proposals/${id}/reject`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mapping-proposals"] }),
  });

  if (pending.length === 0) return null;

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3>Data mapping proposals awaiting review</h3>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        These files didn't match a known shape, so an agent proposed how to map their columns onto real
        telemetry/customer data instead of erroring outright. Nothing has been ingested yet — review the mapping
        below, then approve or reject. An approved mapping is remembered: the next file with this same column
        layout is ingested directly, with no further agent call.
      </p>
      {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}
      {pending.map((p) => (
        <div key={p.id} style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
          <div className="row-between">
            <strong>{p.filename}</strong>
            <div className="row" style={{ gap: 6 }}>
              <Badge text={p.file_kind} />
              <Badge text={`${Math.round(p.confidence * 100)}% confidence`} />
            </div>
          </div>
          <p style={{ fontSize: 13, margin: "6px 0" }}>{p.reasoning}</p>
          <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
            {Object.entries(p.column_roles).map(([col, role]) => `${col} → ${roleLabel(role)}`).join("  ·  ")}
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button onClick={() => approve.mutate(p.id)} disabled={approve.isPending || reject.isPending}>
              {approve.isPending ? "Approving..." : "Approve & ingest"}
            </button>
            <button className="danger" onClick={() => reject.mutate(p.id)} disabled={approve.isPending || reject.isPending}>
              Reject
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DocumentIntake() {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [results, setResults] = useState<FileUploadResult[]>([]);

  const { data: documents, isLoading } = useQuery<DocumentIntakeRow[]>({
    queryKey: ["document-intakes"],
    queryFn: async () => (await api.get("/ingestion/documents")).data,
  });

  const { data: portfolio } = useQuery<PortfolioSnapshot[]>({
    queryKey: ["portfolio-for-documents"],
    queryFn: async () => (await api.get("/twin/portfolio")).data,
  });
  const genAssets = (portfolio || []).flatMap((p) => p.sites.flatMap((s) => s.assets)).filter((a) => a.asset_type === "solar" || a.asset_type === "wind");

  // One file at a time, not Promise.all — a vision extraction call can take
  // several seconds, and running many of those concurrently against the
  // model gateway's per-tenant rate limit would just make most of them fail
  // closed instead of queuing. Different files can be different data
  // sources (a storm advisory PDF alongside a CSV export), each routed to
  // whichever ingestion path actually understands its format.
  const uploadAll = useMutation({
    mutationFn: async (files: File[]) => {
      const outcomes: FileUploadResult[] = [];
      for (const file of files) {
        const suffix = suffixOf(file.name);
        const form = new FormData();
        form.append("file", file);
        try {
          if (TABLE_SUFFIXES.includes(suffix)) {
            const { data } = await api.post("/ingestion/files", form, { headers: { "Content-Type": "multipart/form-data" } });
            const detail = data.detail;
            const message = data.proposal_id
              ? `structure not recognized — proposed as a ${detail?.file_kind} mapping (${Math.round((detail?.confidence || 0) * 100)}% confidence), review below`
              : detail && detail.status !== "ingested"
                ? (detail.message || `${detail.table}: ${detail.status}`)
                : `${data.rows_queued} row(s)/reading(s) processed${detail?.table ? ` for ${detail.table}` : ""}`;
            outcomes.push({ filename: file.name, status: "ok", message });
          } else if (DOCUMENT_SUFFIXES.includes(suffix)) {
            const { data } = await api.post("/ingestion/documents", form, { headers: { "Content-Type": "multipart/form-data" } });
            outcomes.push({ filename: file.name, status: "ok", message: `extracted as ${data.document_type}` });
          } else {
            outcomes.push({ filename: file.name, status: "error", message: `unsupported file type ${suffix || "(none)"} — supported: ${ALL_SUFFIXES.join(", ")}` });
          }
        } catch (err: any) {
          outcomes.push({ filename: file.name, status: "error", message: err?.response?.data?.detail || "upload failed" });
        }
      }
      return outcomes;
    },
    onSuccess: (outcomes) => {
      setResults(outcomes);
      if (fileInput.current) fileInput.current.value = "";
      qc.invalidateQueries({ queryKey: ["document-intakes"] });
      qc.invalidateQueries({ queryKey: ["operations-status"] });
      qc.invalidateQueries({ queryKey: ["mapping-proposals"] });
    },
  });

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Document Intake</h2>
      <p className="muted">
        Upload one or more files from any data source — scanned/photographed PDFs, images or .docx (maintenance
        notices, storm/weather advisories, grid outage notices, inspection reports), read by the same model
        gateway every specialist agent uses; or structured CSV/JSON/XLSX tables, parsed directly or mapped by an
        agent if their structure isn't already recognized. Extractions land here as evidence for review before
        they can affect anything real. This still ingests real data, but no longer starts the optimizer on its
        own — that requires an active database or data_table connector in Connector Studio.
      </p>
      {results.length > 0 && (
        <div className={results.some((r) => r.status === "error") ? "error-banner" : "evidence-box"} style={{ marginBottom: 14 }}>
          {results.map((r, i) => (
            <div key={i} className={results.length > 1 ? "finding" : undefined}>
              {r.status === "ok" ? "✓" : "⚠"} <strong>{r.filename}</strong>: {r.message}
            </div>
          ))}
        </div>
      )}

      <div className="card" style={{ marginBottom: 16, maxWidth: 560 }}>
        <h3>Upload documents / data files</h3>
        <input ref={fileInput} type="file" accept={ALL_SUFFIXES.join(",")} multiple />
        <p className="muted" style={{ fontSize: 11, marginTop: 6, marginBottom: 0 }}>
          Select multiple files at once — each is routed automatically: {TABLE_SUFFIXES.join("/")} as structured
          data (a recognized shape ingests immediately; an unrecognized one is proposed for review below instead
          of erroring), {DOCUMENT_SUFFIXES.join("/")} read directly (image formats and PDF via vision, .docx as text).
        </p>
        <div style={{ marginTop: 10 }}>
          <button
            onClick={() => fileInput.current?.files?.length && uploadAll.mutate(Array.from(fileInput.current.files))}
            disabled={uploadAll.isPending}
          >
            {uploadAll.isPending ? "Uploading..." : "Upload & extract"}
          </button>
        </div>
      </div>

      <MappingProposalsSection />

      {isLoading && <div className="empty-state">Loading...</div>}
      {documents && documents.length === 0 && <div className="empty-state">No documents ingested yet.</div>}
      {documents?.map((doc) => (
        <div key={doc.id} className="card" style={{ marginBottom: 12 }}>
          <div className="row-between">
            <strong>{doc.filename}</strong>
            <div className="row" style={{ gap: 6 }}>
              <Badge text={doc.document_type} />
              <Badge text={doc.severity} />
              <Badge text={doc.status} />
            </div>
          </div>
          <p style={{ fontSize: 13, margin: "8px 0" }}>{doc.summary}</p>
          <div className="muted" style={{ fontSize: 12 }}>
            confidence {(doc.confidence * 100).toFixed(0)}%
            {doc.effective_from && <> · effective {new Date(doc.effective_from).toLocaleString()} → {doc.effective_to ? new Date(doc.effective_to).toLocaleString() : "?"}</>}
            {doc.affected_asset_refs.length > 0 && <> · refs: {doc.affected_asset_refs.join(", ")}</>}
          </div>
          {doc.raw_excerpt && <div className="evidence-box" style={{ marginTop: 8 }}><div className="finding">"{doc.raw_excerpt}"</div></div>}
          {doc.status === "extracted" && <ApplyConstraintForm doc={doc} assets={genAssets} onDone={() => qc.invalidateQueries({ queryKey: ["document-intakes"] })} />}
          {doc.status === "applied" && <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Applied as constraint {doc.constraint_id?.slice(0, 8)} — the optimizer will pick it up on its next cycle.</div>}
        </div>
      ))}
    </div>
  );
}
