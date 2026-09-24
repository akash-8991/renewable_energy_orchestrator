import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/portfolio", label: "Portfolio Operations" },
  { to: "/decisions", label: "Decision Centre" },
  { to: "/approvals", label: "Approval Inbox" },
  { to: "/signals", label: "Live Signal Monitor" },
  { to: "/connectors", label: "Connector Studio" },
  { to: "/policy", label: "Policy Studio" },
  { to: "/simulation", label: "Simulation Lab" },
  { to: "/audit", label: "Audit & Exports" },
  { to: "/tenant", label: "Tenant Administration" },
  { to: "/platform", label: "Platform Operations" },
];

export default function Layout() {
  const { displayName, roles, logout } = useAuth();

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div className="sidebar-brand">
          Renewable Energy Orchestrator
          <small>Platform Console</small>
        </div>
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            {item.label}
          </NavLink>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ padding: "10px", fontSize: 12 }} className="muted">
          {roles.join(", ") || "no roles"}
        </div>
        <button className="secondary" onClick={logout}>
          Sign out
        </button>
      </nav>
      <div className="main">
        <div className="topbar">
          <h1>Renewable Energy Orchestrator</h1>
          <div className="muted">{displayName}</div>
        </div>
        <div className="content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
