import { useEffect } from "react";
import { setToken } from "../api/client";

/** Landed on after `GET /auth/sso/callback` redirects the browser back here
 * with `?token=...` — stores it the same way a password login does, then a
 * full reload so AuthProvider's mount-time hydration (getToken() + GET
 * /auth/me) picks it up, matching exactly what a page refresh after a
 * normal login already does. */
export default function SsoCallback() {
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    setToken(token);
    window.location.href = "/";
  }, []);

  return <div className="empty-state">Signing you in...</div>;
}
