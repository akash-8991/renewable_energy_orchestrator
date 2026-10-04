import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../api/client";
import Badge from "../components/Badge";
import PortfolioRegistry, { PortfolioAssetsTable } from "../components/PortfolioRegistry";

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

  const setActive = useMutation({
    mutationFn: async (v: { id: string; is_active: boolean }) => (await api.patch(`/admin/users/${v.id}`, { is_active: v.is_active })).data,
    onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ["users"] }); },
    onError: (err: any) => setError(err?.response?.data?.detail?.toString() || "Failed to update user"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    createUser.mutate();
  }

  return (
    <div>
      <h2 className="page-title">Tenant Administration</h2>
      <p className="page-intro">Manage this tenant's users and register the sites and assets it operates.</p>
      {error && <div className="error-banner">{error}</div>}

      <div className="card-grid uniform short">
        <form onSubmit={onSubmit} className="card">
          <h3>Add user</h3>
          <div className="card-body">
            <div className="field"><label>Email</label><input value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
            <div className="field"><label>Display name</label><input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required /></div>
            <div className="field"><label>Password (min. 10 characters)</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={10} /></div>
            <div className="field">
              <label>Role</label>
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
          <div className="card-footer"><button type="submit" disabled={createUser.isPending}>Create user</button></div>
        </form>
        <PortfolioRegistry />
      </div>

      <h3 className="section-title">Users in this tenant</h3>
      <div className="table-scroll">
      <table>
        <thead><tr><th>Email</th><th>Name</th><th>Roles</th><th>Active</th><th></th></tr></thead>
        <tbody>
          {(users || []).map((u) => (
            <tr key={u.id}>
              <td>{u.email}</td>
              <td>{u.display_name}</td>
              <td>{u.roles.map((r) => <Badge key={r} text={r} />)}</td>
              <td>{u.is_active ? "yes" : "no"}</td>
              <td>
                <button className="secondary" disabled={setActive.isPending}
                        onClick={() => {
                          if (u.is_active && !window.confirm(`Deactivate ${u.email}? They lose access immediately.`)) return;
                          setActive.mutate({ id: u.id, is_active: !u.is_active });
                        }}>
                  {u.is_active ? "Deactivate" : "Reactivate"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <PortfolioAssetsTable />

      {tenants && (
        <>
          <h3 className="section-title">All platform tenants (platform admin only)</h3>
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
