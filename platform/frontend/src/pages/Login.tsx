import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [tenantSlug, setTenantSlug] = useState("demo-utility");
  const [email, setEmail] = useState("tenant.admin@demo-utility.test");
  const [password, setPassword] = useState("Password123!");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(tenantSlug, email, password);
      navigate("/portfolio");
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-box" onSubmit={onSubmit}>
        <h2 style={{ marginTop: 0 }}>Renewable Energy Orchestrator</h2>
        <p className="muted" style={{ marginTop: -8, fontSize: 13 }}>Platform Console</p>
        {error && <div className="error-banner">{error}</div>}
        <div className="field">
          <label>Tenant slug</label>
          <input value={tenantSlug} onChange={(e) => setTenantSlug(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={{ width: "100%" }} />
        </div>
        <button type="submit" disabled={loading} style={{ width: "100%" }}>
          {loading ? "Signing in..." : "Sign in"}
        </button>
        <p className="muted" style={{ fontSize: 11, marginTop: 14 }}>
          Demo credentials pre-filled. See platform/db/seed.py for the full list of seeded users/roles.
        </p>
      </form>
    </div>
  );
}
