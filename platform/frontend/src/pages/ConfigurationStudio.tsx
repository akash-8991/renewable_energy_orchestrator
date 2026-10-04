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
      <h2 className="page-title">Configuration Studio</h2>
      <p className="page-intro">
        Runtime settings — no redeploy needed. Changes take effect on the next decision cycle or model call
        and are audit-logged like any other governance action. Saving requires the Tenant Admin or
        Platform Admin role.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {saved && <div className="evidence-box"><div className="finding">Configuration saved.</div></div>}

      <div className="card-grid uniform short">
        <div className="card">
          <h3>Live weather feed</h3>
          <p className="card-help">
            Replaces the synthetic solar/wind forecast curves with real forecast data from{" "}
            <a href="https://open-meteo.com" target="_blank" rel="noreferrer">Open-Meteo</a> (free, no API key). Falls
            back to the synthetic model automatically if the feed is disabled, the site coordinates aren't set, or the
            API call fails.
          </p>
          <div className="card-body">
            <label className="check">
              <input type="checkbox" checked={form.live_weather_enabled} onChange={(e) => set("live_weather_enabled", e.target.checked)} />
              Enable live weather feed
            </label>
            <div className="field-row" style={{ marginTop: 14 }}>
              <div className="field">
                <label>Site latitude</label>
                <input type="number" step="any" value={form.weather_site_lat ?? ""} placeholder="e.g. 51.5074"
                       onChange={(e) => set("weather_site_lat", e.target.value === "" ? null : Number(e.target.value))} />
              </div>
              <div className="field">
                <label>Site longitude</label>
                <input type="number" step="any" value={form.weather_site_lon ?? ""} placeholder="e.g. -0.1278"
                       onChange={(e) => set("weather_site_lon", e.target.value === "" ? null : Number(e.target.value))} />
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <h3>Model gateway resilience</h3>
          <p className="card-help">
            A circuit breaker and per-call timeout around every LLM call, so a slow or down provider degrades a decision
            cycle gracefully instead of stalling it. After consecutive failures, later calls fail immediately until the
            cooldown elapses.
          </p>
          <div className="card-body">
            <label className="check">
              <input type="checkbox" checked={form.gateway_circuit_breaker_enabled} onChange={(e) => set("gateway_circuit_breaker_enabled", e.target.checked)} />
              Enable circuit breaker
            </label>
            <div className="field-row" style={{ marginTop: 14 }}>
              <div className="field">
                <label>Call timeout (s)</label>
                <input type="number" min={1} max={300} value={form.gateway_timeout_seconds} onChange={(e) => set("gateway_timeout_seconds", Number(e.target.value))} />
              </div>
              <div className="field">
                <label>Failure threshold</label>
                <input type="number" min={1} max={50} value={form.gateway_failure_threshold} onChange={(e) => set("gateway_failure_threshold", Number(e.target.value))} />
              </div>
              <div className="field">
                <label>Cooldown (s)</label>
                <input type="number" min={5} max={3600} value={form.gateway_cooldown_seconds} onChange={(e) => set("gateway_cooldown_seconds", Number(e.target.value))} />
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <h3>Identity provider (SSO)</h3>
          <p className="card-help">
            {form.sso_available
              ? "An OIDC identity provider is configured for this deployment. Choose whether this tenant's users may log in via SSO, in addition to email and password."
              : "No OIDC identity provider is configured for this deployment (OIDC_ISSUER is unset), so this setting has no effect until one is."}
          </p>
          <div className="card-body">
            <label className="check">
              <input type="checkbox" checked={form.sso_enabled} disabled={!form.sso_available} onChange={(e) => set("sso_enabled", e.target.checked)} />
              Enable SSO login for this tenant
            </label>
          </div>
        </div>
      </div>

      <div className="row" style={{ gap: 14 }}>
        <button onClick={onSubmit} disabled={save.isPending}>{save.isPending ? "Saving..." : "Save configuration"}</button>
        <span className="muted" style={{ fontSize: 13 }}>
          {form.updated_by ? `Last updated by ${form.updated_by} at ${new Date(form.updated_at).toLocaleString()}` : "Not yet customized for this tenant — showing defaults."}
        </span>
      </div>
    </div>
  );
}
