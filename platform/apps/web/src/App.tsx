import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import ApprovalInbox from "./pages/ApprovalInbox";
import AuditExports from "./pages/AuditExports";
import ConnectorStudio from "./pages/ConnectorStudio";
import DecisionCentre from "./pages/DecisionCentre";
import LiveSignalMonitor from "./pages/LiveSignalMonitor";
import Login from "./pages/Login";
import PlatformOperations from "./pages/PlatformOperations";
import PolicyStudio from "./pages/PolicyStudio";
import PortfolioOperations from "./pages/PortfolioOperations";
import SimulationLab from "./pages/SimulationLab";
import TenantAdministration from "./pages/TenantAdministration";

function RequireAuth({ children }: { children: JSX.Element }) {
  const { isAuthenticated, loading } = useAuth();
  if (loading) return <div className="empty-state">Loading...</div>;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return children;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Navigate to="/portfolio" replace />} />
        <Route path="/portfolio" element={<PortfolioOperations />} />
        <Route path="/decisions" element={<DecisionCentre />} />
        <Route path="/approvals" element={<ApprovalInbox />} />
        <Route path="/signals" element={<LiveSignalMonitor />} />
        <Route path="/connectors" element={<ConnectorStudio />} />
        <Route path="/policy" element={<PolicyStudio />} />
        <Route path="/simulation" element={<SimulationLab />} />
        <Route path="/audit" element={<AuditExports />} />
        <Route path="/tenant" element={<TenantAdministration />} />
        <Route path="/platform" element={<PlatformOperations />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
