import { useCallback, useEffect, useReducer } from "react";
import { api, FleetState, Vehicle, ZoneCount } from "./api";

export interface State {
  vehicles: Record<string, Vehicle>;
  zones: Record<string, ZoneCount>;
  fleet: FleetState;
  wsStatus: "connecting" | "open" | "closed";
  lastEventAt: string | null;
}

type Action =
  | { type: "seed"; vehicles: Vehicle[]; zones: ZoneCount[]; fleet: FleetState }
  | {
      type: "vehicle_update";
      payload: {
        id: string;
        status: Vehicle["status"];
        battery_pct?: number;
        last_seen_at: string;
      };
    }
  | { type: "zone_count_update"; payload: { zone_id: string; entry_count: number } }
  | {
      type: "anomaly";
      payload: {
        vehicle_id: string;
        ts: string;
        kind?: string;
        severity?: Vehicle["latest_anomaly_severity"];
      };
    }
  | { type: "ws_status"; status: State["wsStatus"] };

const initial: State = {
  vehicles: {},
  zones: {},
  fleet: { idle: 0, moving: 0, charging: 0, fault: 0, total: 0 },
  wsStatus: "connecting",
  lastEventAt: null,
};

function recomputeFleet(vehicles: Record<string, Vehicle>): FleetState {
  const s: FleetState = { idle: 0, moving: 0, charging: 0, fault: 0, total: 0 };
  for (const v of Object.values(vehicles)) {
    s[v.status] += 1;
    s.total += 1;
  }
  return s;
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "seed": {
      const vehicles: Record<string, Vehicle> = {};
      for (const v of action.vehicles) vehicles[v.id] = v;
      const zones: Record<string, ZoneCount> = {};
      for (const z of action.zones) zones[z.zone_id] = z;
      return { ...state, vehicles, zones, fleet: action.fleet };
    }
    case "vehicle_update": {
      const prev = state.vehicles[action.payload.id];
      const next: Vehicle = {
        id: action.payload.id,
        status: action.payload.status,
        battery_pct:
          action.payload.battery_pct !== undefined
            ? action.payload.battery_pct
            : prev?.battery_pct ?? null,
        last_seen_at: action.payload.last_seen_at,
        latest_anomaly_kind: prev?.latest_anomaly_kind ?? null,
        latest_anomaly_ts: prev?.latest_anomaly_ts ?? null,
        latest_anomaly_severity: prev?.latest_anomaly_severity ?? null,
      };
      const vehicles = { ...state.vehicles, [next.id]: next };
      return {
        ...state,
        vehicles,
        fleet: recomputeFleet(vehicles),
        lastEventAt: action.payload.last_seen_at,
      };
    }
    case "zone_count_update": {
      return {
        ...state,
        zones: {
          ...state.zones,
          [action.payload.zone_id]: {
            zone_id: action.payload.zone_id,
            entry_count: action.payload.entry_count,
            updated_at: new Date().toISOString(),
          },
        },
      };
    }
    case "anomaly": {
      const prev = state.vehicles[action.payload.vehicle_id];
      if (!prev) return state;
      const next: Vehicle = {
        ...prev,
        latest_anomaly_kind: action.payload.kind ?? prev.latest_anomaly_kind,
        latest_anomaly_ts: action.payload.ts,
        latest_anomaly_severity:
          action.payload.severity ?? prev.latest_anomaly_severity,
      };
      return {
        ...state,
        vehicles: { ...state.vehicles, [next.id]: next },
        lastEventAt: action.payload.ts,
      };
    }
    case "ws_status":
      return { ...state, wsStatus: action.status };
  }
}

export function useFleetStore() {
  const [state, dispatch] = useReducer(reducer, initial);

  const reseed = useCallback(async () => {
    try {
      const [vehicles, zones, fleet] = await Promise.all([
        api.vehicles(),
        api.zones(),
        api.fleet(),
      ]);
      dispatch({ type: "seed", vehicles, zones, fleet });
    } catch (e) {
      console.error("seed failed", e);
    }
  }, []);

  useEffect(() => {
    reseed();
  }, [reseed]);

  return { state, dispatch, reseed };
}
