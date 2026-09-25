import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";

interface TrendPoint {
  bucket_time: string;
  generation_kw: number;
  demand_kw: number;
  battery_kw: number;
}

const PRESETS = [
  { label: "1h", minutes: 60 },
  { label: "6h", minutes: 6 * 60 },
  { label: "24h", minutes: 24 * 60 },
  { label: "7d", minutes: 7 * 24 * 60 },
] as const;

// A rough one-tick-per-~60-buckets target so the chart stays readable
// whether it's spanning an hour or a week, without the caller having to
// think about it.
function bucketMinutesFor(rangeMinutes: number): number {
  return Math.min(1440, Math.max(1, Math.round(rangeMinutes / 60)));
}

function toLocalInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function PortfolioTrendChart() {
  const [presetMinutes, setPresetMinutes] = useState<number>(6 * 60);
  const [customRange, setCustomRange] = useState<{ since: string; until: string } | null>(null);

  const { since, until } = useMemo(() => {
    if (customRange) return customRange;
    const now = new Date();
    const from = new Date(now.getTime() - presetMinutes * 60_000);
    return { since: from.toISOString(), until: now.toISOString() };
  }, [presetMinutes, customRange]);

  const rangeMinutes = Math.max(1, (new Date(until).getTime() - new Date(since).getTime()) / 60_000);
  const bucketMinutes = bucketMinutesFor(rangeMinutes);

  const { data, isLoading, error } = useQuery<TrendPoint[]>({
    queryKey: ["portfolio-trend", since, until, bucketMinutes],
    queryFn: async () =>
      (await api.get("/twin/trend", { params: { since, until, bucket_minutes: bucketMinutes } })).data,
    refetchInterval: 30000,
  });

  const chartData = (data || []).map((p) => ({
    ...p,
    label: new Date(p.bucket_time).toLocaleString(undefined, {
      month: rangeMinutes > 24 * 60 ? "short" : undefined,
      day: rangeMinutes > 24 * 60 ? "numeric" : undefined,
      hour: "2-digit",
      minute: "2-digit",
    }),
  }));

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="row-between" style={{ marginBottom: 4, flexWrap: "wrap", gap: 10 }}>
        <h3 style={{ margin: 0 }}>Generation, Demand &amp; Battery Trend</h3>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              className={presetMinutes === p.minutes && !customRange ? "" : "secondary"}
              style={{ padding: "5px 10px", fontSize: 12 }}
              onClick={() => {
                setCustomRange(null);
                setPresetMinutes(p.minutes);
              }}
            >
              {p.label}
            </button>
          ))}
          <input
            type="datetime-local"
            style={{ fontSize: 12, padding: "5px 8px" }}
            value={customRange ? toLocalInputValue(new Date(customRange.since)) : ""}
            onChange={(e) => {
              if (!e.target.value) return;
              const since = new Date(e.target.value).toISOString();
              const until = customRange?.until || new Date().toISOString();
              setCustomRange({ since, until });
            }}
          />
          <span className="muted" style={{ fontSize: 12 }}>to</span>
          <input
            type="datetime-local"
            style={{ fontSize: 12, padding: "5px 8px" }}
            value={customRange ? toLocalInputValue(new Date(customRange.until)) : ""}
            onChange={(e) => {
              if (!e.target.value) return;
              const until = new Date(e.target.value).toISOString();
              const since = customRange?.since || new Date(Date.now() - presetMinutes * 60_000).toISOString();
              setCustomRange({ since, until });
            }}
          />
        </div>
      </div>

      {isLoading && <div className="empty-state">Loading trend...</div>}
      {error && <div className="error-banner">Failed to load trend data.</div>}
      {!isLoading && !error && chartData.length === 0 && (
        <div className="empty-state">No telemetry in this range yet.</div>
      )}
      {!isLoading && chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="genGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#16a34a" stopOpacity={0.35} />
                <stop offset="95%" stopColor="#16a34a" stopOpacity={0.02} />
              </linearGradient>
              <linearGradient id="demandGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#2563eb" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#2563eb" stopOpacity={0.02} />
              </linearGradient>
              <linearGradient id="battGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#7c3aed" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e1e5ec" />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#667085" }} minTickGap={30} />
            <YAxis tick={{ fontSize: 11, fill: "#667085" }} width={56} label={{ value: "kW", angle: -90, position: "insideLeft", fontSize: 11, fill: "#667085" }} />
            <Tooltip
              contentStyle={{ background: "#fff", border: "1px solid #e1e5ec", borderRadius: 8, fontSize: 12 }}
              formatter={(value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 0 }) + " kW"}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Area type="monotone" dataKey="generation_kw" name="Generation" stroke="#16a34a" fill="url(#genGradient)" strokeWidth={2} />
            <Area type="monotone" dataKey="demand_kw" name="Demand" stroke="#2563eb" fill="url(#demandGradient)" strokeWidth={2} />
            <Area type="monotone" dataKey="battery_kw" name="Battery net" stroke="#7c3aed" fill="url(#battGradient)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
