import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";

interface PlatformSettingsView {
  live_weather_enabled: boolean;
  weather_site_lat: number | null;
  weather_site_lon: number | null;
  gateway_circuit_breaker_enabled: boolean;
  gateway_timeout_seconds: number;
  gateway_failure_threshold: number;
  gateway_cooldown_seconds: number;
  sso_enabled: boolean;
  sso_available: boolean;
  updated_by: string | null;
  updated_at: string;
}

export default function ConfigurationStudio() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const { data, isLoading } = useQuery<PlatformSettingsView>({
    queryKey: ["platform-settings"],
    queryFn: async () => (await api.get("/configuration/settings")).data,
  });

  const [form, setForm] = useState<PlatformSettingsView | null>(null);
  useEffect(() => {
    if (data && !form) setForm(data);
  }, [data, form]);

  const save = useMutation({
    mutationFn: async (body: Partial<PlatformSettingsView>) => (await api.put("/configuration/settings", body)).data,
    onSuccess: (updated: PlatformSettingsView) => {
      setForm(updated);
      setError(null);
      setSaved(true);
      qc.invalidateQueries({ queryKey: ["platform-settings"] });
      setTimeout(() => setSaved(false), 3000);
    },
    onError: (err: any) => setError(err?.response?.data?.detail || "Failed to save configuration"),
  });

  if (isLoading || !form) return <div className="empty-state">Loading configuration...</div>;

  function set<K extends keyof PlatformSettingsView>(key: K, value: PlatformSettingsView[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }

  function onSubmit() {
    if (!form) return;
    save.mutate({
      live_weather_enabled: form.live_weather_enabled,
      weather_site_lat: form.weather_site_lat,
      weather_site_lon: form.weather_site_lon,
      gateway_circuit_breaker_enabled: form.gateway_circuit_breaker_enabled,
      gateway_timeout_seconds: form.gateway_timeout_seconds,
      gateway_failure_threshold: form.gateway_failure_threshold,
      gateway_cooldown_seconds: form.gateway_cooldown_seconds,
      sso_enabled: form.sso_enabled,
    });
  }

  return (
    <div>
      <h2 style={{ fontSize: 15 }}>Configuration Studio</h2>
      <p className="muted">
        Runtime-configurable settings behind the platform's production-readiness punch list — no
        redeploy needed. Changes take effect on the next decision cycle / model call and are
        audit-logged like any other governance action. Requires the <code>manage:settings</code>{" "}
        permission (Tenant Admin) or <code>manage:platform_config</code> (Platform Admin) to save.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {saved && <div className="evidence-box"><div className="finding">Configuration saved.</div></div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>Live weather feed</h3>
        <p className="muted" style={{ fontSize: 12 }}>
          Replaces the synthetic solar/wind forecast curves with real forecast data from{" "}
          <a href="https://open-meteo.com" target="_blank" rel="noreferrer">Open-Meteo</a> (free,
          no API key). Falls back to the synthetic model automatically if the feed is disabled, the
          site coordinates aren't set, or the API call fails for any reason.
        </p>
        <label className="row" style={{ gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={form.live_weather_enabled}
                 onChange={(e) => set("live_weather_enabled", e.target.checked)} />
          Enable live weather feed
        </label>
        <div className="row" style={{ gap: 16, marginTop: 10, flexWrap: "wrap" }}>
          <div className="field">
            <label>Site latitude</label>
            <input type="number" step="any" value={form.weather_site_lat ?? ""}
                   onChange={(e) => set("weather_site_lat", e.target.value === "" ? null : Number(e.target.value))}
                   placeholder="e.g. 51.5074" style={{ width: 160 }} />
          </div>
          <div className="field">
            <label>Site longitude</label>
            <input type="number" step="any" value={form.weather_site_lon ?? ""}
                   onChange={(e) => set("weather_site_lon", e.target.value === "" ? null : Number(e.target.value))}
                   placeholder="e.g. -0.1278" style={{ width: 160 }} />
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>Model gateway resilience</h3>
        <p className="muted" style={{ fontSize: 12 }}>
          A circuit breaker + per-call timeout around every LLM call, so a slow or down provider
          degrades a decision cycle gracefully instead of stalling it. Opens after consecutive
          failures for this tenant; later calls fail immediately (zero tokens spent) until the
          cooldown elapses.
        </p>
        <label className="row" style={{ gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={form.gateway_circuit_breaker_enabled}
                 onChange={(e) => set("gateway_circuit_breaker_enabled", e.target.checked)} />
          Enable circuit breaker
        </label>
        <div className="row" style={{ gap: 16, marginTop: 10, flexWrap: "wrap" }}>
          <div className="field">
            <label>Call timeout (seconds)</label>
            <input type="number" min={1} max={300} value={form.gateway_timeout_seconds}
                   onChange={(e) => set("gateway_timeout_seconds", Number(e.target.value))} style={{ width: 120 }} />
          </div>
          <div className="field">
            <label>Failure threshold</label>
            <input type="number" min={1} max={50} value={form.gateway_failure_threshold}
                   onChange={(e) => set("gateway_failure_threshold", Number(e.target.value))} style={{ width: 120 }} />
          </div>
          <div className="field">
            <label>Cooldown (seconds)</label>
            <input type="number" min={5} max={3600} value={form.gateway_cooldown_seconds}
                   onChange={(e) => set("gateway_cooldown_seconds", Number(e.target.value))} style={{ width: 120 }} />
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>Identity provider (SSO)</h3>
        <p className="muted" style={{ fontSize: 12 }}>
          {form.sso_available
            ? "An OIDC identity provider is configured for this deployment. Toggle whether this tenant's users may log in via SSO, in addition to email/password."
            : "No OIDC identity provider is configured for this deployment (OIDC_ISSUER is unset) — this toggle has no effect until one is. See docs/DEPLOYMENT.md's SSO section."}
        </p>
        <label className="row" style={{ gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={form.sso_enabled} disabled={!form.sso_available}
                 onChange={(e) => set("sso_enabled", e.target.checked)} />
          Enable SSO login for this tenant
        </label>
      </div>

      <button onClick={onSubmit} disabled={save.isPending}>{save.isPending ? "Saving..." : "Save configuration"}</button>
      <p className="muted" style={{ fontSize: 11, marginTop: 10 }}>
        {form.updated_by ? `Last updated by ${form.updated_by} at ${new Date(form.updated_at).toLocaleString()}` : "Not yet customized for this tenant — showing defaults."}
      </p>
    </div>
  );
}
