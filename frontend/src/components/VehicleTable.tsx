import { Vehicle, VehicleStatus, AnomalySeverity } from "../api";

const statusClasses: Record<VehicleStatus, string> = {
  idle: "bg-slate-700 text-slate-200",
  moving: "bg-emerald-700 text-emerald-50",
  charging: "bg-sky-700 text-sky-50",
  fault: "bg-red-700 text-red-50",
};

const severityClasses: Record<AnomalySeverity, string> = {
  info: "text-slate-300",
  warning: "text-amber-400",
  critical: "text-red-400",
};

function batteryColor(pct: number | null): string {
  if (pct === null) return "bg-slate-700";
  if (pct < 15) return "bg-red-500";
  if (pct < 40) return "bg-amber-500";
  return "bg-emerald-500";
}

function timeAgo(iso: string | null, now: number): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  const dt = Math.max(0, (now - t) / 1000);
  if (dt < 2) return "now";
  if (dt < 60) return `${dt.toFixed(0)}s ago`;
  if (dt < 3600) return `${(dt / 60).toFixed(0)}m ago`;
  return `${(dt / 3600).toFixed(0)}h ago`;
}

export function VehicleTable({
  vehicles,
  now,
}: {
  vehicles: Vehicle[];
  now: number;
}) {
  return (
    <div className="rounded-lg border border-slate-800 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-900/60 text-slate-400 uppercase text-xs tracking-wider">
          <tr>
            <th className="text-left px-3 py-2">Vehicle</th>
            <th className="text-left px-3 py-2">Status</th>
            <th className="text-left px-3 py-2 w-44">Battery</th>
            <th className="text-left px-3 py-2">Last seen</th>
            <th className="text-left px-3 py-2">Latest anomaly</th>
          </tr>
        </thead>
        <tbody>
          {vehicles.map((v) => (
            <tr
              key={v.id}
              className="border-t border-slate-800 hover:bg-slate-900/40"
            >
              <td className="px-3 py-2 font-mono">{v.id}</td>
              <td className="px-3 py-2">
                <span
                  className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium ${
                    statusClasses[v.status]
                  }`}
                >
                  {v.status}
                </span>
              </td>
              <td className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <div className="w-24 h-2 rounded bg-slate-800 overflow-hidden">
                    <div
                      className={`h-full ${batteryColor(v.battery_pct)}`}
                      style={{ width: `${v.battery_pct ?? 0}%` }}
                    />
                  </div>
                  <span className="tabular-nums text-slate-300 w-8 text-right">
                    {v.battery_pct ?? "—"}
                  </span>
                </div>
              </td>
              <td className="px-3 py-2 text-slate-400 tabular-nums">
                {timeAgo(v.last_seen_at, now)}
              </td>
              <td className="px-3 py-2">
                {v.latest_anomaly_kind ? (
                  <span
                    className={
                      v.latest_anomaly_severity
                        ? severityClasses[v.latest_anomaly_severity]
                        : "text-slate-300"
                    }
                  >
                    {v.latest_anomaly_kind}
                    <span className="text-slate-500 ml-2">
                      {timeAgo(v.latest_anomaly_ts, now)}
                    </span>
                  </span>
                ) : (
                  <span className="text-slate-600">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
