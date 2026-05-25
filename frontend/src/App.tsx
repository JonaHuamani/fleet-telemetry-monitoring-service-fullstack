import { useEffect, useState } from "react";
import { api } from "./api";
import { FleetSummary } from "./components/FleetSummary";
import { VehicleTable } from "./components/VehicleTable";
import { ZoneCounts } from "./components/ZoneCounts";
import { useFleetStore } from "./store";
import { useFleetWebSocket } from "./useWebSocket";

function App() {
  const { state, dispatch, reseed } = useFleetStore();
  useFleetWebSocket(dispatch, { onReconnect: reseed });

  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const vehicles = Object.values(state.vehicles).sort((a, b) => {
    const an = parseInt(a.id.replace(/^v-/, ""), 10);
    const bn = parseInt(b.id.replace(/^v-/, ""), 10);
    return an - bn;
  });
  const zones = Object.values(state.zones).sort((a, b) =>
    a.zone_id.localeCompare(b.zone_id),
  );

  return (
    <div className="min-h-screen px-6 py-6 max-w-7xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Qualitara Fleet Telemetry</h1>
          <p className="text-sm text-slate-400">
            50 vehicles · live dashboard ·{" "}
            <span
              className={
                state.wsStatus === "open"
                  ? "text-emerald-400"
                  : state.wsStatus === "connecting"
                  ? "text-amber-400"
                  : "text-red-400"
              }
            >
              WS {state.wsStatus}
            </span>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => api.startSim().catch(console.error)}
            className="px-3 py-1.5 rounded-md bg-emerald-700 hover:bg-emerald-600 text-sm"
          >
            Start sim
          </button>
          <button
            onClick={() => api.stopSim().catch(console.error)}
            className="px-3 py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 text-sm"
          >
            Stop sim
          </button>
          <button
            onClick={() =>
              api.burst("charging_bay_1", 20, 20).catch(console.error)
            }
            className="px-3 py-1.5 rounded-md bg-sky-700 hover:bg-sky-600 text-sm"
          >
            Burst 20 → charging_bay_1
          </button>
        </div>
      </header>

      <section>
        <FleetSummary fleet={state.fleet} />
      </section>

      <section className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <h2 className="text-sm uppercase tracking-wider text-slate-400 mb-2">
            Vehicles
          </h2>
          <VehicleTable vehicles={vehicles} now={now} />
        </div>
        <div>
          <h2 className="text-sm uppercase tracking-wider text-slate-400 mb-2">
            Zone entries
          </h2>
          <ZoneCounts zones={zones} />
        </div>
      </section>
    </div>
  );
}

export default App;
