export type VehicleStatus = "idle" | "moving" | "charging" | "fault";
export type AnomalySeverity = "info" | "warning" | "critical";

export interface Vehicle {
  id: string;
  status: VehicleStatus;
  battery_pct: number | null;
  last_seen_at: string | null;
  latest_anomaly_kind: string | null;
  latest_anomaly_ts: string | null;
  latest_anomaly_severity: AnomalySeverity | null;
}

export interface FleetState {
  idle: number;
  moving: number;
  charging: number;
  fault: number;
  total: number;
}

export interface ZoneCount {
  zone_id: string;
  entry_count: number;
  updated_at: string | null;
}

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const api = {
  fleet: () => get<FleetState>("/fleet/state"),
  vehicles: () => get<Vehicle[]>("/vehicles"),
  zones: () => get<ZoneCount[]>("/zones/counts"),
  startSim: (tick_hz = 1.0, vehicle_count = 50) =>
    post<{ started: boolean }>("/admin/simulator/start", { tick_hz, vehicle_count }),
  stopSim: () => post<{ stopped: boolean }>("/admin/simulator/stop", {}),
  burst: (zone_id: string, vehicle_count: number, jitter_ms = 0) =>
    post<{ events_sent: number }>("/admin/simulator/burst", {
      zone_id,
      vehicle_count,
      jitter_ms,
    }),
  setStatus: (vehicle_id: string, status: VehicleStatus, reason?: string) =>
    post(`/vehicles/${vehicle_id}/status`, { status, reason }),
  createMission: (vehicle_id: string) =>
    post(`/missions`, { vehicle_id }),
};
