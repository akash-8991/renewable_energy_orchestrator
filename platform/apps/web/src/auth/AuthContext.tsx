import React, { createContext, useContext, useEffect, useState } from "react";
import { getToken, login as apiLogin, me, setToken } from "../api/client";

interface AuthState {
  isAuthenticated: boolean;
  loading: boolean;
  displayName: string | null;
  roles: string[];
  permissions: string[];
  login: (tenantSlug: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  hasPermission: (p: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [roles, setRoles] = useState<string[]>([]);
  const [permissions, setPermissions] = useState<string[]>([]);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    me()
      .then((data) => {
        setIsAuthenticated(true);
        setRoles(data.roles || []);
        setPermissions(data.permissions || []);
        setDisplayName(data.display_name || data.email || data.user_id);
      })
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(tenantSlug: string, email: string, password: string) {
    const data = await apiLogin(tenantSlug, email, password);
    setToken(data.access_token);
    setIsAuthenticated(true);
    setRoles(data.roles);
    setDisplayName(data.display_name);
    const profile = await me();
    setPermissions(profile.permissions || []);
  }

  function logout() {
    setToken(null);
    setIsAuthenticated(false);
    setRoles([]);
    setPermissions([]);
    setDisplayName(null);
  }

  function hasPermission(p: string) {
    return permissions.includes(p);
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, loading, displayName, roles, permissions, login, logout, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
