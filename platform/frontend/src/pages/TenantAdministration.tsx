import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";

interface UserRow { id: string; email: string; display_name: string; roles: string[]; is_active: boolean }
interface TenantRow { id: string; slug: string; name: string; deployment_mode: string; created_at: string }

const ROLES = ["viewer", "operator", "senior_operator", "portfolio_manager", "ot_admin", "model_admin", "tenant_admin", "auditor_dpo"];

export default function TenantAdministration() {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("operator");
  const [error, setError] = useState<string | null>(null);

  const { data: users } = useQuery<UserRow[]>({ queryKey: ["users"], queryFn: async () => (await api.get("/admin/users")).data });
  const { data: tenants } = useQuery<TenantRow[]>({
    queryKey: ["tenants"],
    queryFn: async () => (await api.get("/admin/tenants")).data,
    retry: false,
  });

  const createUser = useMutation({
    mutationFn: async () => (await api.post("/admin/users", { email, display_name: displayName, password, roles: [role] })).data,
    onSuccess: () => {
      setError(null);
      setEmail("");
      setDisplayName("");
      setPassword("");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (err: any) => setError(err?.response?.data?.detail?.toString() || "Failed to create user"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    createUser.mutate();
  }

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Tenant Administration</h2>
      {error && <div className="error-banner">{error}</div>}

      <form onSubmit={onSubmit} className="card" style={{ maxWidth: 480, marginBottom: 20 }}>
        <h3>Add user</h3>
        <div className="field"><label>Email</label><input value={email} onChange={(e) => setEmail(e.target.value)} required style={{ width: "100%" }} /></div>
        <div className="field"><label>Display name</label><input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required style={{ width: "100%" }} /></div>
        <div className="field"><label>Password</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required style={{ width: "100%" }} /></div>
        <div className="field">
          <label>Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value)} style={{ width: "100%" }}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <button type="submit" disabled={createUser.isPending}>Create user</button>
      </form>

      <h3 style={{ fontSize: 13, textTransform: "uppercase", color: "var(--text-dim)" }}>Users in this tenant</h3>
      <div className="table-scroll">
      <table>
        <thead><tr><th>Email</th><th>Name</th><th>Roles</th><th>Active</th></tr></thead>
        <tbody>
          {(users || []).map((u) => (
            <tr key={u.id}>
              <td>{u.email}</td>
              <td>{u.display_name}</td>
              <td>{u.roles.map((r) => <Badge key={r} text={r} />)}</td>
              <td>{u.is_active ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      {tenants && (
        <>
          <h3 style={{ fontSize: 13, textTransform: "uppercase", color: "var(--text-dim)", marginTop: 24 }}>
            All platform tenants (platform_admin only)
          </h3>
          <div className="table-scroll">
          <table>
            <thead><tr><th>Slug</th><th>Name</th><th>Mode</th><th>Created</th></tr></thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id}>
                  <td className="mono">{t.slug}</td>
                  <td>{t.name}</td>
                  <td>{t.deployment_mode}</td>
                  <td>{new Date(t.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}
