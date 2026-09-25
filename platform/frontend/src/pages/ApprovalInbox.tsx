import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface Approval {
  id: string; decision_id: string; action_id: string | null; outcome: string;
  requires_second_approver: boolean; expires_at: string; created_at: string;
}

export default function ApprovalInbox() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const { data, isLoading } = useQuery<Approval[]>({
    queryKey: ["approvals", "pending"],
    queryFn: async () => (await api.get("/governance/approvals", { params: { outcome: "pending" } })).data,
    refetchInterval: 10000,
  });

  const decide = useMutation({
    mutationFn: async ({ id, outcome, reason }: { id: string; outcome: string; reason?: string }) =>
      (await api.post(`/governance/approvals/${id}/decide`, { outcome, reason })).data,
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (err: any) => setError(err?.response?.data?.detail || "Decision failed"),
  });

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Approval Inbox</h2>
      <p className="muted">Actions awaiting human sign-off before they can be dispatched to the OT command gateway.</p>
      {error && <div className="error-banner">{error}</div>}
      {isLoading && <div className="empty-state">Loading...</div>}
      {data && data.length === 0 && <div className="empty-state">Nothing pending approval right now.</div>}
      {data && data.length > 0 && (
        <table>
          <thead>
            <tr><th>Requested</th><th>Decision</th><th>Action</th><th>Four-eyes</th><th>Expires</th><th></th></tr>
          </thead>
          <tbody>
            {data.map((a) => (
              <tr key={a.id}>
                <td>{new Date(a.created_at).toLocaleTimeString()}</td>
                <td className="mono">{a.decision_id.slice(0, 8)}</td>
                <td className="mono">{a.action_id?.slice(0, 8) || "—"}</td>
                <td>{a.requires_second_approver ? <Badge text="required" /> : "—"}</td>
                <td>{new Date(a.expires_at).toLocaleTimeString()}</td>
                <td className="row">
                  <button onClick={() => decide.mutate({ id: a.id, outcome: "approved" })} disabled={decide.isPending}>
                    Approve
                  </button>
                  <button className="secondary" onClick={() => decide.mutate({ id: a.id, outcome: "held" })} disabled={decide.isPending}>
                    Hold
                  </button>
                  <button className="danger" onClick={() => decide.mutate({ id: a.id, outcome: "rejected" })} disabled={decide.isPending}>
                    Reject
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
