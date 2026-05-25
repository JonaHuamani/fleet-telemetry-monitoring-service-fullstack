# Qualitara Fleet Telemetry Monitoring

A small vertical slice of a fleet telemetry monitoring service for 50 autonomous industrial vehicles emitting telemetry at 1 Hz. Built as a take-home project — see `ADR.md` for the reasoning, and `AI_LOG.md` for the AI interaction log.

**Stack:** FastAPI · PostgreSQL 16 · WebSockets · React + TypeScript + Tailwind (Vite). One `docker compose up` for the backend, one `npm run dev` for the dashboard.

---

## Prerequisites

- Docker (with Compose v2)
- Node.js 18+ and npm (for the frontend dev server)

That's it. Everything else lives in containers.

---

## Run it

### 1. Backend + database

```bash
docker compose up -d --build
```

This starts:

- `postgres` on host port **5433** (mapped from container 5432 to avoid clashing with a local Postgres on 5432 — adjust in `docker-compose.yml` if needed).
- `backend` on host port **8000**. The FastAPI app runs migrations and seeds on startup (50 vehicles `v-1`…`v-50`, 20 zone-count rows initialized to 0).

Wait a few seconds, then sanity-check:

```bash
curl -s http://localhost:8000/healthz                # {"status":"ok"}
curl -s http://localhost:8000/fleet/state            # {"idle":50,"moving":0,...,"total":50}
curl -s http://localhost:8000/zones/counts | head -c 200
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The dashboard seeds initial state via HTTP, then subscribes to `/ws/stream` for live deltas (both proxied by Vite to the backend).

### 3. Drive the demo

From the dashboard header:

- **Start sim** — `POST /admin/simulator/start`, spawns an in-process loop at 1 Hz that walks all 50 vehicles (battery decay, occasional zone transitions, occasional status flips) and ingests events through the same `telemetry.ingest()` function used by `POST /telemetry`.
- **Burst 20 → charging_bay_1** — fires 20 concurrent zone-entry events at the same zone in the same instant. The counter increments by exactly 20.
- **Stop sim** — kills the loop.

You can also drive it from the CLI:

```bash
# Create an active mission, then transition the vehicle to fault.
curl -s -X POST http://localhost:8000/missions \
  -H 'Content-Type: application/json' \
  -d '{"vehicle_id":"v-12"}'

curl -s -X POST http://localhost:8000/vehicles/v-12/status \
  -H 'Content-Type: application/json' \
  -d '{"status":"fault","reason":"demo"}'

# Inspect the result.
docker compose exec postgres psql -U qualitara -d qualitara -c \
  "SELECT v.status, m.status AS mission_status, mr.reason
   FROM vehicles v
   LEFT JOIN missions m ON m.vehicle_id = v.id
   LEFT JOIN maintenance_records mr ON mr.vehicle_id = v.id
   WHERE v.id = 'v-12';"
```

---

## Verification checklist

These were exercised during development. Each one is reproducible from the CLI above or the dashboard buttons.

| Check | How |
|---|---|
| 50-vehicle concurrent burst against the same zone increments by exactly 50, no lost entries | 5 parallel `POST /admin/simulator/burst` (10 each) on the same zone, then read `/zones/counts` |
| Aggregate fleet state always sums to 50 | `curl /fleet/state` |
| Fault transition atomically cancels the active mission and opens a maintenance record | the two `curl`s above + the `psql` join |
| Repeated fault for an already-faulted vehicle does not create a duplicate maintenance record | second `POST /vehicles/v-12/status` with `status=fault`; `SELECT COUNT(*) FROM maintenance_records WHERE vehicle_id='v-12'` stays at 1 |
| Older telemetry does not roll vehicle state backwards | send a telemetry POST with a `timestamp` earlier than `vehicles.last_seen_at`; the row is inserted but `vehicles.status` / `last_seen_at` do not change |
| Duplicate `event_id` is idempotent (no double zone count, no second anomaly) | repeat the same POST with the same `event_id`; second response returns `"idempotent": true` |
| Anomalies are detectable and filterable | `GET /anomalies?vehicle_id=v-1&since=…` |
| Persistence across restart | `docker compose restart backend` — state intact, migrations idempotent |
| Live updates within ~1 s on the dashboard | open the dashboard, click "Start sim", watch the counters move |
| Dashboard recovers from a dropped WebSocket | `docker compose restart backend` with the dashboard open; on reconnect it re-fetches `/fleet/state`, `/vehicles`, `/zones/counts` before applying new deltas |
| Immutable telemetry rows agree with denormalized zone counts after quiescence | `SELECT COUNT(*) FROM telemetry WHERE zone_entered = 'X'` vs `SELECT entry_count FROM zone_counts WHERE zone_id = 'X'` |

---

## API surface (quick reference)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/telemetry` | Ingest one event. Concurrency-critical — see ADR §1.2. Accepts optional `event_id` (UUID) for idempotent retry. |
| `GET`  | `/anomalies` | Filter by `vehicle_id`, `since`, `until`, `limit`. |
| `POST` | `/vehicles/{id}/status` | Explicit status update. `fault` triggers the fault path. |
| `GET`  | `/vehicles` | List with current status + battery + latest anomaly. |
| `GET`  | `/fleet/state` | Per-status counts. |
| `GET`  | `/zones/counts` | Per-zone entry counts. |
| `POST` | `/missions` | Create active mission for a vehicle. |
| `GET`  | `/missions` | Filter by `vehicle_id`, `status`. |
| `POST` | `/admin/simulator/start` · `/stop` · `/burst` | Drive the embedded simulator. |
| `WS`   | `/stream` | Broadcasts `vehicle_update`, `anomaly`, `zone_count_update`, `mission_update`. |

Interactive docs at <http://localhost:8000/docs>.

### Telemetry payload notes

- `timestamp` is the **vehicle-reported event time**; the database also records a server-side `ingested_at` on every row for auditing and replay detection.
- `event_id` is optional. If a client supplies it, ingest becomes idempotent — repeated POSTs with the same `event_id` return the same telemetry row id with `"idempotent": true` and do **not** re-apply zone counters, vehicle-state updates, or anomalies.
- Out-of-order events (a POST whose `timestamp` is earlier than `vehicles.last_seen_at`) are persisted into the immutable `telemetry` table and still count towards zone entries, but they do not roll vehicle state backwards or fire transition-based anomalies.

---

## Project layout

```
backend/
  app/
    constants.py      # ZONES, anomaly thresholds, vehicle IDs
    db.py             # asyncpg pool + migration runner
    main.py           # FastAPI app, lifespan, CORS, stale-telemetry loop
    models.py         # Pydantic schemas
    ws.py             # broadcast manager
    routes/           # one module per resource
    services/         # transactional service layer (telemetry, vehicles, missions, anomalies, simulator)
  migrations/0001_init.sql
  Dockerfile
  requirements.txt
frontend/
  src/
    App.tsx
    api.ts            # tiny fetch wrapper
    store.ts          # useReducer-based fleet store
    useWebSocket.ts   # WS hook with exponential-backoff reconnect
    components/       # FleetSummary, VehicleTable, ZoneCounts
  vite.config.ts      # proxies /api and /ws to backend:8000
ADR.md
AI_LOG.md
README.md
docker-compose.yml
```

---

## Resetting state

```bash
# Wipe telemetry + anomalies + missions + maintenance_records, reset counts.
docker compose exec postgres psql -U qualitara -d qualitara -c "
  TRUNCATE telemetry, anomalies, missions, maintenance_records RESTART IDENTITY CASCADE;
  UPDATE zone_counts SET entry_count = 0;
  UPDATE vehicles SET status = 'idle', battery_pct = 100, last_seen_at = NULL;"

# Or nuke the volume entirely.
docker compose down -v
```
