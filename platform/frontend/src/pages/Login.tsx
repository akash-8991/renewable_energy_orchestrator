import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiBaseUrl } from "../api/client";
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
      <div className="login-brand">
        <div className="login-brand-mark">⚡</div>
        <h1>Renewable Energy Orchestrator</h1>
        <p>
          Decision and control platform coordinating solar, wind, battery storage, demand
          flexibility, grid interconnection and market participation — with a governed agent
          layer that explains every recommendation and a deterministic optimizer that stays in
          sole control of anything that touches equipment.
        </p>
        <ul className="login-brand-features">
          <li>Live portfolio telemetry with freshness-scored digital twin</li>
          <li>Explainable, evidence-backed decisions on every cycle</li>
          <li>Role-based approval workflow with independent OT safety validation</li>
        </ul>
      </div>
      <div className="login-panel">
        <form className="login-box" onSubmit={onSubmit}>
          <h2 style={{ marginTop: 0, marginBottom: 2, fontSize: 20 }}>Sign in</h2>
          <p className="muted" style={{ marginTop: 0, marginBottom: 22, fontSize: 13 }}>Platform Console</p>
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
          <button
            type="button"
            className="secondary"
            style={{ width: "100%", marginTop: 10 }}
            onClick={() => { window.location.href = `${apiBaseUrl}/auth/sso/login?tenant_slug=${encodeURIComponent(tenantSlug)}`; }}
          >
            Log in with SSO
          </button>
          <p className="muted" style={{ fontSize: 11, marginTop: 16 }}>
            Demo credentials pre-filled. See <code className="mono">docs/DEPLOYMENT.md</code> (§ A7) for the
            full table of seeded users/roles.
          </p>
        </form>
      </div>
    </div>
  );
}
