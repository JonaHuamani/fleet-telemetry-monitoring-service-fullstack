import { ZoneCount } from "../api";

export function ZoneCounts({ zones }: { zones: ZoneCount[] }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {zones.map((z) => (
        <div
          key={z.zone_id}
          className="rounded-md border border-slate-800 bg-slate-900/40 p-3 min-w-0"
        >
          <div
            title={z.zone_id}
            className="text-xs uppercase tracking-wide text-slate-400 font-mono leading-tight break-words"
          >
            {z.zone_id.replace(/_/g, " ")}
          </div>
          <div className="text-2xl font-semibold tabular-nums mt-1">
            {z.entry_count}
          </div>
        </div>
      ))}
    </div>
  );
}
