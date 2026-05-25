import { FleetState } from "../api";

const TILES: { key: keyof FleetState; label: string; cls: string }[] = [
  { key: "idle", label: "Idle", cls: "bg-slate-800 border-slate-700" },
  { key: "moving", label: "Moving", cls: "bg-emerald-900/40 border-emerald-700" },
  { key: "charging", label: "Charging", cls: "bg-sky-900/40 border-sky-700" },
  { key: "fault", label: "Fault", cls: "bg-red-900/40 border-red-700" },
];

export function FleetSummary({ fleet }: { fleet: FleetState }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {TILES.map((t) => (
        <div
          key={t.key}
          className={`rounded-lg border p-4 ${t.cls}`}
        >
          <div className="text-xs uppercase tracking-wider text-slate-400">
            {t.label}
          </div>
          <div className="text-3xl font-semibold mt-1 tabular-nums">
            {fleet[t.key]}
          </div>
        </div>
      ))}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="text-xs uppercase tracking-wider text-slate-400">Total</div>
        <div className="text-3xl font-semibold mt-1 tabular-nums">{fleet.total}</div>
      </div>
    </div>
  );
}
