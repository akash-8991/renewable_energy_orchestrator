/**
 * Scaffolding placeholder — the 10 dashboard workspaces (Portfolio
 * Operations, Decision Centre, Approval Inbox, Live Signal Monitor,
 * Connector Studio, Policy Studio, Simulation Lab, Audit & Exports, Tenant
 * Administration, Platform Operations) are wired up in the "Frontend" build
 * phase, once the backend APIs they call exist.
 */
export default function App() {
  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>Renewable Energy Orchestrator</h1>
      <p>
        Platform scaffolding is up. Backend: <code>{import.meta.env.VITE_API_BASE_URL}</code>
      </p>
      <p>Dashboard workspaces land once the underlying APIs are built out.</p>
    </div>
  );
}
