# Architecture Decision Record — Fleet Telemetry Monitoring Service

**Status:** accepted · **Scope:** take-home vertical slice · **Time-boxed to:** 5–6 hours.

This ADR records the load-bearing decisions for the slice, the open points in the spec and how this implementation resolved them, and the parts that are deliberately out of scope.

---

## 1. Load-bearing decisions

### 1.1 PostgreSQL, not SQLite

The spec stresses concurrent ingest, atomic mission cancellation on `fault`, and the "correct isolation strategy." Those are easier to argue and easier to implement correctly on PG's MVCC, with explicit `SELECT … FOR UPDATE` row locks and real `SERIALIZABLE` available if ever needed. SQLite + WAL would be fine for the demo's throughput (50 vehicles × 1 Hz) but treats every write as a global write lock, which is exactly the property the spec is asking us *not* to lean on. We chose to spend the setup tax (Docker Compose) once and get a database whose concurrency model matches the problem statement.

### 1.2 Hybrid zone counting: immutable telemetry + denormalized counter, same transaction

The spec is explicit that every zone entry must be counted under concurrent writers. The simplest correct solution is to do *both* things in the same transaction:

- The `telemetry` row is the immutable source of truth (`zone_entered` set or null).
- `zone_counts.entry_count` is incremented with `UPDATE … SET entry_count = entry_count + 1 WHERE zone_id = $1`. PG takes a row-level lock on that one zone row, so concurrent updates serialize per-zone (no fleet-wide contention) while different zones proceed in parallel.

This gives O(1) reads on `GET /zones/counts` regardless of telemetry table size, and the immutable telemetry table means the counter can always be rebuilt if it ever diverged. (We verified this experimentally: 50 concurrent zone-entry events landing on the same zone counter incremented it by exactly 50, with no lost or duplicated entries.)

### 1.3 WebSockets, server-push only, with HTTP as the source of truth

A polling dashboard at 1 s gives the right latency for human eyes, but it scales linearly with viewers and it can hide bursty behavior: a flurry of zone entries between two polls appears as an unexplained jump. A single broadcast channel pushes deltas as they happen and demonstrates the real burst behavior the system was built to handle. WebSocket messages are best-effort dashboard deltas, not the source of truth. The database-backed HTTP endpoints remain canonical. On initial connect and every reconnect, the dashboard re-fetches `/fleet/state`, `/vehicles`, and `/zones/counts` before applying new deltas. This allows the UI to recover from missed WebSocket messages or broadcast failures.

### 1.4 Isolation level

`READ COMMITTED` with explicit row-level locks is sufficient because every cross-row invariant is protected by locking the rows being mutated: vehicle, active mission, and zone counter. `SERIALIZABLE` would add retry complexity without improving correctness for these specific invariants.

---

## 2. Things the spec leaves open, and how this implementation resolves them

The spec is deliberately concise. The decisions below are explicit so a reviewer can see exactly where this implementation extends the contract and why.

### 2.1 Event time vs. ingest time

Telemetry stores both vehicle-reported event time (`ts`) and server-side ingest time (`ingested_at`). Event time is used for domain analysis and user-facing telemetry history. Ingest time is used for operational auditing and detecting delayed or replayed telemetry. In production, vehicle clocks would need NTP guarantees or server-side ordering rules; this slice records both so the ambiguity is visible in the data rather than hidden in the application code.

### 2.2 Duplicate delivery and out-of-order events

The spec does not include a stable event ID. This implementation guarantees no lost zone increments for accepted concurrent requests, but it does not provide exactly-once processing under duplicate delivery unless the client supplies an `event_id`. `TelemetryIn.event_id` is optional; when present, a partial unique index on `telemetry.event_id` makes ingest idempotent — a duplicate POST resolves to the same telemetry row and does not re-apply zone counters, vehicle-state updates, or anomalies. A production deployment should make `event_id` mandatory and enforce uniqueness before applying any side effects.

Out-of-order events are persisted. The current vehicle state (`status`, `battery_pct`, `last_seen_at`) is only overwritten by telemetry whose `ts` is newer than or equal to the current `last_seen_at`. An older event still produces an immutable `telemetry` row and still increments the zone counter (zone entries are a per-event aggregate), but it does not roll vehicle state backwards or trigger transition-based anomalies against stale context.

### 2.3 Mission model

Mission is intentionally modeled as the minimum entity needed to demonstrate the required invariant: at most one active mission per vehicle (enforced by a partial unique index), cancellable atomically when the vehicle transitions to fault. A production AGV mission would include route, waypoints, payload, priority, SLA, assignment history, and completion/cancellation workflows.

### 2.4 Where a fault transition originates

A vehicle can transition to fault through telemetry (`POST /telemetry` with `status = "fault"`) or through an explicit status update endpoint (`POST /vehicles/{id}/status`). Both paths use the same transactional fault workflow and both emit a `FAULT_STATUS` anomaly. The workflow only runs on an actual transition from non-fault to fault, so repeated fault events for an already-faulted vehicle do not produce duplicate maintenance records.

### 2.5 Fault with no active mission

A fault always opens a maintenance record. If no active mission exists, mission cancellation is a no-op and the maintenance record is created with `mission_id = NULL`. A fault is always operationally interesting; the absence of a mission does not suppress it.

### 2.6 `STALE_TELEMETRY` for never-seen vehicles

Seeded vehicles with no telemetry are not marked stale immediately, to avoid noisy startup behavior in the demo. A production system would use a startup grace period and then mark never-seen vehicles as stale if they still have no telemetry within an operationally meaningful window.

### 2.7 Anomaly definitions and re-emission

Six rules cover both flavors the spec hinted at — threshold and pattern — and emit on *transition* (or per-event for `RAPID_BATTERY_DROP`) so the table is not flooded while a vehicle stays in a faulted or low-battery state. The rules are: `LOW_BATTERY` (warning < 15 %, critical < 5 %), `FAULT_STATUS` (status flipped to `fault`), `ERROR_CODES_PRESENT` (set became non-empty), `OVERSPEED` (`speed_mps > 5.0` — a typical industrial AGV ceiling), `RAPID_BATTERY_DROP` (>20 pp in <60 s vs. the previous event), and `STALE_TELEMETRY` (no event for >10 s, detected by a 5 s background scan with re-emission throttled).

This slice does not implement a full per-vehicle/per-anomaly cooldown window. Production would likely suppress repeated anomalies of the same kind within a configurable interval to absorb status flapping (e.g. fault → idle → fault within seconds).

### 2.8 Vehicle status enum

Vehicle status is restricted to the four values defined by the spec — `idle`, `moving`, `charging`, `fault` — and enforced by a `CHECK` constraint in PG. Unknown statuses are rejected by both Pydantic and the database.

### 2.9 Initial fleet membership

The fleet is fixed for this slice and seeded at startup as `v-1` through `v-50`. This keeps the dashboard and aggregate fleet state deterministic. Unknown vehicle IDs are rejected (404) by every endpoint that takes a `vehicle_id`; on-the-fly vehicle creation is intentionally disallowed so a typo cannot silently corrupt fleet state or zone counters. A production system would load fleet membership from a fleet registry or provisioning system.

### 2.10 Ingest rate and backpressure

The implementation targets the stated workload: 50 vehicles emitting at 1 Hz plus short concurrent bursts. Requests are processed synchronously and backpressure is naturally applied by the Postgres connection pool. At significantly higher sustained rates, ingestion should move to a durable queue or log, and side effects such as zone counts, vehicle state, and anomaly evaluation should be processed asynchronously.

### 2.11 Single-event POST

`POST /telemetry` accepts one event per request. This matches the stated model of vehicles emitting telemetry independently. Batch ingestion for offline replay (a vehicle reconnecting after a network gap and flushing its local buffer) is out of scope and would be added as `POST /telemetry/batch` in production.

### 2.12 Field types and validation

`battery_pct` is treated as a percentage in the range 0–100, matching the examples in the spec. `speed_mps` is non-negative. `zone_entered` must either be null or one of the hardcoded zones. Unknown vehicle IDs and invalid zones are rejected to avoid corrupting fleet state or zone counters. These constraints are enforced both at the Pydantic boundary and via PG `CHECK` constraints.

---

## 3. What would change if scale grew significantly

"Significantly" here means three orders of magnitude on writes — say 50 000 vehicles instead of 50, sustained. The current shape would break in four predictable places, and we would address each in isolation rather than rewriting.

1. **Ingest path.** A single PG transaction per event saturates at some single-primary ceiling we have not measured for this slice. We would split it: append-only telemetry writes go through a durable log (Kafka or equivalent) and a downstream consumer applies the side effects (zone counts, vehicle row updates, anomaly evaluation). The acknowledgement to the client becomes "we durably accepted your event," not "we processed your event."
2. **Zone counter contention.** At very high write rates, a single row per zone becomes a hotspot. We would shard each zone's counter across N sub-rows and aggregate on read (the classic high-volume counter pattern), or move to a streaming aggregator (Flink, Materialize) that materializes per-zone counts.
3. **Read path for `/vehicles`.** The `LEFT JOIN LATERAL` on `anomalies` is fine at 50 vehicles; at 50 000 it would need a denormalized `latest_anomaly_*` column on `vehicles` maintained in the same transaction, or a separate read store.
4. **WebSocket fan-out.** One process broadcasting to thousands of dashboards needs either a pub/sub backbone (Redis pub/sub, NATS, Kafka with per-client offsets) or a sticky-session WS layer with per-client filtering.

For "10× scale," none of the above is necessary — the current design holds.

---

## 4. Deliberately out of scope

- **Authentication and authorization.** Intentionally out of scope for the take-home. In production, telemetry ingestion, admin simulator endpoints, and operator status updates would each require distinct authorization policies.
- **Exhaustive automated tests.** Verified end-to-end manually: 50-vehicle concurrent burst against a single zone counter produces exactly 50 entries (no losses); fault transition atomically cancels the mission and opens a maintenance record; fleet/state always sums to 50; the immutable telemetry count and the denormalized counter agree once the system quiesces; repeated fault events do not produce duplicate maintenance records. The first follow-up would be a small integration suite covering: concurrent zone increments are not lost; repeated fault does not create a second maintenance record; older telemetry does not overwrite current vehicle state; duplicate `event_id` is idempotent.
- **Pagination.** `/vehicles` returns all vehicles because the slice is fixed at 50. `/anomalies` and `/telemetry` support `limit` because those datasets grow over time. At larger scale, vehicle listing would need pagination, filtering, or a dedicated read model.
- **Historical retention.** Telemetry and anomalies are retained indefinitely in this slice. Production would partition both tables by time and apply retention or purge policies.
- **Mission completion.** Only mission creation and cancellation-via-fault are wired. A real system would have `complete` and `cancel-by-operator` operations.
- **TLS, secrets management, observability, metrics, structured logging beyond stdlib.**
- **A historical timeline view** on the dashboard — current state only.
- **Frontend tests, error toasts, retry UX.** The client logs to the console on failure and re-seeds via HTTP on every WebSocket reconnect.

The driving principle for the cut list: keep the core invariants the spec was actually testing (concurrent zone counting, atomic fault transition, safe aggregate fleet state, real-time updates) demonstrable and correct, and let the rest be visibly absent so the reviewer can tell what we know we left out.
