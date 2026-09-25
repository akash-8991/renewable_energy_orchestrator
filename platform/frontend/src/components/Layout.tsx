import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/portfolio", label: "Portfolio Operations" },
  { to: "/decisions", label: "Decision Centre" },
  { to: "/customers", label: "Customer Decisions" },
  { to: "/approvals", label: "Approval Inbox" },
  { to: "/signals", label: "Live Signal Monitor" },
  { to: "/actions", label: "Action Tickets" },
  { to: "/connectors", label: "Connector Studio" },
  { to: "/documents", label: "Document Intake" },
  { to: "/policy", label: "Policy Studio" },
  { to: "/simulation", label: "Simulation Lab" },
  { to: "/observability", label: "Agent Observability" },
  { to: "/audit", label: "Audit & Exports" },
  { to: "/tenant", label: "Tenant Administration" },
  { to: "/platform", label: "Platform Operations" },
];

export default function Layout() {
  const { displayName, roles, logout } = useAuth();
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();

  // A route change is the clearest signal the drawer did its job — close it
  // on mobile so the next screen isn't hidden behind it.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  return (
    <div className="app-shell">
      <nav className={"sidebar" + (navOpen ? " open" : "")}>
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
      <div className={"sidebar-overlay" + (navOpen ? " open" : "")} onClick={() => setNavOpen(false)} />
      <div className="main">
        <div className="topbar">
          <div className="row" style={{ gap: 10 }}>
            <button className="menu-toggle" aria-label="Toggle navigation" onClick={() => setNavOpen((v) => !v)}>
              ☰
            </button>
            <h1>Renewable Energy Orchestrator</h1>
          </div>
          <div className="muted">{displayName}</div>
        </div>
        <div className="content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
