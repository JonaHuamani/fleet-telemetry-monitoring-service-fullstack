# AI Interaction Log

This file records every meaningful interaction with AI tools during the Qualitara fullstack take-home project, along with corrections, redirections, and a final reflection.

**Primary AI tool:** Claude Code (Opus 4.7, 1M context) running in the local terminal.
**Working language:** The human and the AI conversed in Spanish; this log is kept in English because the deliverable is for an English-speaking reviewer. Prompts are translated/paraphrased faithfully.

---

## Session 1 — Project intake and planning

### Prompt 1 — "Can you access this page?"
The user asked whether I could read the take-home spec hosted on a public Notion page (`https://qualitara.notion.site/fullstack-take-home-project`).

**AI output:** Attempted `WebFetch`. Notion renders content client-side via JavaScript, so the fetched HTML only contained the literal string `Notion` with no body. I reported this back and offered three workarounds: paste the content directly, export from Notion as Markdown/PDF, or share a static HTML mirror.

**Correction / redirection:** None — the limitation was understood. The user pasted the full spec text into the chat.

---

### Prompt 2 — Full spec pasted into chat
The user pasted the complete take-home prompt: build a fleet telemetry monitoring service (Python backend + React/TS dashboard + ADR + AI log), with detailed requirements for telemetry ingestion, anomaly detection, zone-traversal counting, atomic fault transitions, aggregate fleet state, and a 5–6 hour budget. The `ZONES` constant (20 zones) was included.

**AI output:** Produced a structured summary of the four deliverables (backend, frontend, ADR, AI log) and the key constraints. Surfaced three open questions before writing any code:
1. The AI log itself is a deliverable, so we should write it as we go rather than reconstruct it at the end.
2. Asked how the user wanted to collaborate: (a) full architectural plan up front, (b) tight iterative loop, or (c) targeted assistance while the user drives.
3. Asked about stack preference; floated FastAPI + SQLite (WAL) + WebSockets as a plausible default but offered to discuss tradeoffs.

**Correction / redirection:** None. The user's reply set the working rules:
- Keep the AI log in English even though we converse in Spanish.
- Choose path (a): nail down architecture and definitions before writing code.
- Discuss stack alternatives with pros/cons before committing.

---

## Session 2 — Stack discussion

### Prompt 3 — "Discuss the stack first; evaluate alternatives with pros and cons"
The user opted for the architecture-first path and asked for a discussion of stack alternatives before any implementation began. They also instructed that the AI log be kept in English even though the chat itself is in Spanish, because the deliverable is for an English-speaking reviewer.

**AI output:** Produced a structured comparison across five layers — backend framework (FastAPI vs Django REST), database (SQLite + WAL vs Postgres), zone-counting strategy (derived vs denormalized), dashboard transport (polling vs WebSockets), and frontend stack (Vite + React + TS + Tailwind). For each, listed pros, cons, and an initial recommendation. Default recommendations: FastAPI, SQLite + WAL, derived counts, polling at 1 s, Vite/React/TS/Tailwind.

**Correction / redirection:** The user disagreed on three of the five recommendations and pushed for the production-realistic choice in each case:
- Postgres over SQLite, justified as "the right decision, not the fast one — tech debt has a cost for the client."
- Denormalized zone counter over derived, on the same reasoning.
- WebSockets over polling, justified as "1 s polling under burst events can mislead the user."

The AI accepted all three with refinements:
- Postgres: add Docker Compose so the reviewer still has one-command setup.
- Denormalized counter: keep telemetry as the immutable source of truth and update both tables in the same transaction; the counter is for O(1) reads, not a replacement for the audit trail.
- WebSockets: stay disciplined — server-push only, no presence/auth, basic reconnect, with an HTTP seed on connect.

The AI also surfaced three decisions the user had not yet made (anomaly definition, mission lifecycle, telemetry generator approach) and listed concrete options for each.

---

### Prompt 4 — Decisions on the three open items
User chose:
- **Anomaly: A2** (threshold + pattern hybrid) — to demonstrate business understanding.
- **Mission: B2** (`POST /missions` + `GET /missions` endpoints) — minimal create/list, no full CRUD.
- **Telemetry generator: C2** (embedded admin endpoints), with willingness to fall back to a standalone script if it costs too much time.

**AI output:** Finalized the implementation plan file `1-framework-fastapi-es-merry-cloud.md` covering data model, endpoints, the `POST /telemetry` transaction shape, the `fault` path, frontend architecture, a verification plan, the list of files to create, and an explicit out-of-scope list for the ADR. Also flagged that A2 + B2 + C2 places the project at the upper end of the 5–6 h budget and listed the discipline points that will keep it from overflowing (no component library, ~50-line WS manager, embedded simulator capped at 45 min net before fallback, tests deferred until the happy path is verified end-to-end).

**Correction / redirection:** None. Plan approved by the user.

---

## Session 3 — Implementation

### Prompt 5 — "Plan approved, start coding"
After the plan file was approved by the user (with the user editing nothing), the implementation phase opened with the standard `Start with updating your todo list if applicable` hint from the harness.

**AI output:** Created a task list with 11 items mapping 1:1 to the plan's phases (scaffold → data layer → telemetry ingest → missions/fault → reads → WS + stale loop → simulator → frontend scaffold → frontend components → e2e verification → docs). Marked each task `in_progress` before starting and `completed` after manual verification. Wrote the project in roughly this order:

1. **Scaffold** — `docker-compose.yml` (postgres 16-alpine + backend), `backend/Dockerfile` (python:3.12-slim, uvicorn), `requirements.txt` (FastAPI 0.115, asyncpg 0.30, pydantic 2.10), `.gitignore`, package layout.
2. **Constants and models** — `app/constants.py` (ZONES verbatim from spec, anomaly thresholds, vehicle IDs), `app/models.py` (Pydantic schemas for telemetry, vehicles, missions, anomalies, simulator).
3. **Migrations** — `migrations/0001_init.sql`: six tables, all indexes (including the partial unique index that enforces "at most one active mission per vehicle"), seeds for 50 vehicles and 20 zone-count rows. `db.py` runs `*.sql` files in order and records applied ones in `_migrations`.
4. **WS broadcaster** — `app/ws.py`, ~40 lines: an `asyncio.Lock`-guarded set of connections, `connect`/`disconnect`/`broadcast`, dead-connection sweep on send failure.
5. **Anomaly rules** — `app/services/anomalies.py`: pure function `evaluate_synchronous_rules(...)` returning `AnomalyToEmit` dataclasses (no DB I/O), then `insert_anomalies(conn, ...)` writes them. Transition-based emission for `LOW_BATTERY`, `FAULT_STATUS`, `ERROR_CODES_PRESENT`, `OVERSPEED`; per-event for `RAPID_BATTERY_DROP`.
6. **Mission service** — `app/services/missions.py`: `create_mission` (catches `UniqueViolationError` → `MissionConflict`), `cancel_active_mission_and_open_maintenance` (called from within an open transaction that already holds `FOR UPDATE` on the vehicle row; takes `FOR UPDATE` on the active mission row, then cancels it and inserts a maintenance record).
7. **Telemetry ingest** — `app/services/telemetry.py`: the heart of the system. Single transaction at `READ COMMITTED` with explicit row locks; one `SELECT … FOR UPDATE` on the vehicle row to serialize per-vehicle transition detection; reads the previous telemetry row for the comparisons that need `prev_speed_mps` and `prev_error_codes`; inserts telemetry; bumps `zone_counts` with a row-level lock if `zone_entered` is set; evaluates anomalies; updates the vehicle row; if status crossed into `fault`, runs the fault path inline. Returns a result struct that the route uses to decide what to broadcast over WS.
8. **Vehicles service** — `app/services/vehicles.py`: explicit `update_status`, `list_vehicles` (with a `LEFT JOIN LATERAL` for each vehicle's latest anomaly), `fleet_state` (single grouped SELECT), `zone_counts`, and `detect_stale_vehicles` (the background scan that emits `STALE_TELEMETRY` anomalies with a re-emission throttle).
9. **Simulator** — `app/services/simulator.py`: a per-vehicle random-walk state machine, a 1 Hz loop using `asyncio.gather` for genuine concurrency at the DB layer, and a `burst()` function that fires N concurrent zone-entry events at the same zone.
10. **Routes** — one file per resource under `app/routes/`. Glued the broadcasts to the service results.
11. **`main.py`** — FastAPI lifespan that initializes the pool, runs migrations, spawns the stale-telemetry loop, and tears everything down cleanly on shutdown.

**Smoke tests at the backend layer** (run via `curl` against `docker compose up`):

- Single telemetry POST: returned `{"id":1,"anomalies_emitted":0}`. Good.
- Telemetry POST with `zone_entered`: returned `{"id":2}` and `zone_counts.charging_bay_1` went from 0 to 1.
- Telemetry POST with `battery_pct: 10`: returned `{"id":3,"anomalies_emitted":1}` and `GET /anomalies` showed the `LOW_BATTERY` warning.
- Mission creation + fault transition: returned `{"became_fault":true,"cancelled_mission_id":1,"maintenance_record_id":1}`; `psql` confirmed the mission flipped to `cancelled` and the maintenance record was linked.
- Burst of 20 vehicles into the same zone: `zone_counts` incremented by exactly 20.
- 5 concurrent bursts of 10 each into the same zone (50 total, fired with `&` in shell): `zone_counts` incremented by exactly 50. **This is the core spec invariant.**

**Correction / redirection during implementation:**

- The AI wrote `[__import__("json").dumps(...)]` in `insert_anomalies` as a quick hack to do a batch insert with `UNNEST`. It self-flagged this as ugly, then rewrote the function as a small `for` loop of single inserts. Justification: anomaly emission per event is 0–1 rows in practice, so the batch was premature optimization.
- The AI added an unused `import json` to `missions.py` and removed it on review.
- The AI initially wrote a buggy reducer case in `frontend/src/store.ts` for `zone_count_update` that contained duplicate object keys (`entry_count` and `updated_at` declared twice). Self-spotted on read-back and fixed.
- The first `docker compose up` failed with "Bind for 0.0.0.0:5432 failed: port is already allocated" — a Postgres was already listening on the host. Remapped to `5433:5432` in `docker-compose.yml` and re-ran.

### Prompt 6 — Frontend
**AI output:** Vite + React 18 + TS + Tailwind. `api.ts` is a 30-line typed `fetch` wrapper. `store.ts` is a single `useReducer` with one action per WS message kind plus a `seed` action fed by `Promise.all` on the three HTTP GETs. `useWebSocket.ts` uses `wss?://${host}/ws/stream` (proxied by Vite) with exponential-backoff reconnect capped at 10 s. Three components: `FleetSummary` (5 tiles), `VehicleTable` (50 rows: status badge, battery bar, last-seen tick, latest anomaly), `ZoneCounts` (20 cards). The header has three buttons to drive the simulator from the dashboard.

**Verification:** type-check passes (`npx tsc -b`); Vite serves on 5173; HTTP and WS proxy both work (`HTTP/1.1 101 Switching Protocols` confirmed via raw `curl`); the WS frames carry live `vehicle_update`, `zone_count_update`, `anomaly`, and `mission_update` payloads.

### Prompt 7 — Docs (ADR, README, this log)
**AI output:** A one-page ADR covering the three load-bearing decisions (PG over SQLite, hybrid zone counting, WebSockets), explicit assumptions where the spec was open, a scale-up section with four specific bottlenecks, and a declared out-of-scope list. The README is a flat run-through: prerequisites, two `docker compose` / `npm` commands, a CLI demo, a verification checklist, the API surface, the project layout, and a reset recipe.

---

## Session 4 — Ambiguity audit and follow-up changes

### Prompt 8 — "What did we leave implicit? Audit the assumptions."
After the slice was running end-to-end, the user asked for a self-audit of every place the spec was open and we made a silent decision. The AI produced a 17-item list (A1–A17) classified by impact: arguably architectural (timestamps, duplicate delivery, mission domain, fault origin, stale detection for never-seen vehicles, ingest backpressure), implementation-level (anomaly re-emission, isolation, batch POST, status enum, initial fleet), and surface-level (auth, pagination, field validation, retention, WS recovery).

The audit was reviewer-facing: each item named what the spec said (or didn't), what this implementation chose, the alternatives, and a frank evaluation of whether the chosen path was the best one. A few items called the original choice out as weak — most notably (A1) trusting client timestamps unconditionally, (A2) the lack of an `event_id` for idempotent retry, and (A4) the silent asymmetry between the telemetry-driven and operator-driven fault paths.

### Prompt 9 — "Address A1–A17. Minimal, safe changes only; document the rest."
The user laid out an explicit response strategy per item: implement the small, safe fixes; document the rest as assumptions or production follow-ups; do not introduce Kafka, batch ingestion, auth, or any large architectural change; and do not claim unmeasured throughput numbers in the ADR.

**AI output — code changes (applied):**

1. **A1 + A2 — new migration `0002_event_id_and_ingested_at.sql`:** added `ingested_at TIMESTAMPTZ DEFAULT NOW()` to `telemetry` and `anomalies`; added optional `event_id UUID` to `telemetry` with a partial unique index `WHERE event_id IS NOT NULL`. The existing migration runner picks it up at startup because it scans `migrations/*.sql` in lexicographic order and records applied files in `_migrations`.
2. **A2 — idempotent ingest:** `TelemetryIn` gained an optional `event_id: UUID | None`. `TelemetryAck` gained an `idempotent: bool` flag. The ingest service catches `asyncpg.UniqueViolationError` on the telemetry insert, looks up the existing row by `event_id`, and returns an `IngestResult` with `idempotent=True` and no side effects. The route short-circuits before any broadcast in that case.
3. **A2 — out-of-order guard:** `ingest()` now branches on `event.timestamp >= prev_last_seen` (or `prev_last_seen IS NULL`). If the event is older, the telemetry row is still inserted and the zone counter is still bumped (entries are an immutable per-event aggregate), but the vehicle row is not updated and anomaly evaluation is skipped. Returned via a new `applied_to_state` flag on `IngestResult` so the route can suppress the `vehicle_update` broadcast accordingly.
4. **A4 — uniform `FAULT_STATUS` emission:** `services.vehicles.update_status` now inserts a `FAULT_STATUS` anomaly inside its transaction when `became_fault` is true (with `details.source = "status_update"`). The route broadcasts it alongside the `mission_update`. Both the telemetry-driven and operator-driven fault paths now emit the same anomaly, removing the previous silent asymmetry.
5. **A17 — frontend re-seed on reconnect:** `useFleetStore` exposes a `reseed` callback; `useFleetWebSocket` accepts an `onReconnect` option and invokes it on every `ws.onopen` *after* the first disconnect. The first connect still relies on the mount-time seed to avoid a double fetch. The dashboard now self-heals from a backend restart by re-fetching `/fleet/state`, `/vehicles`, and `/zones/counts` before applying further WS deltas.

**AI output — documentation changes (applied):**

- The ADR was reorganised into three sections: load-bearing decisions, points the spec leaves open with the explicit resolution this implementation chose, and an enumerated out-of-scope list. The wording for each open point matches the agreed reviewer-facing phrasing: "the spec leaves this open; this implementation assumes…" rather than "we should have asked".
- The README's verification checklist gained four rows: repeated fault does not double-create maintenance records, older telemetry does not roll state backwards, duplicate `event_id` is idempotent, and the dashboard recovers from a dropped WebSocket. A short "Telemetry payload notes" subsection documents the `event_id`, `ingested_at`, and out-of-order semantics.

**AI output — what was *not* done, and why:**

- **A6 startup grace period for never-seen vehicles** was documented as a known limitation rather than implemented. The current behaviour (only vehicles with a non-null `last_seen_at` are candidates for `STALE_TELEMETRY`) keeps demo startup quiet; the user explicitly said "do not make startup noisy for the demo, but document the trade-off."
- **A8 anomaly cooldown window** was documented as the production follow-up. Implementing it cleanly requires either a `last_emitted_at` column per (vehicle, kind) or a window scan against `anomalies`; both are noticeable diffs for a behaviour the spec does not require.
- **Automated tests** were deferred. The project has no pre-existing pytest scaffolding; adding pytest-asyncio + DB fixtures for three integration tests is more scaffolding than the slice warrants. The ADR's out-of-scope section names the first follow-up test suite explicitly (concurrent zone increments, repeated fault, out-of-order guard, duplicate `event_id`). The high-value invariant — concurrent zone increments not lost — was verified manually with a 50-vehicle burst before this audit.
- **Batch POST** (A10), **mission domain expansion** (A3), **auth** (A13), **retention/partitioning** (A16), and **historical view** were left in the out-of-scope list of the ADR.

### Prompt 10 — Final A1–A17 status table

| # | Finding | Decision | Implemented now? | Documented? | Production follow-up |
|---|---|---|---|---|---|
| A1 | Vehicle vs. server timestamp | Store both: `ts` (event) and `ingested_at` (server) | Yes — migration 0002 + ADR §2.1 | Yes | Enforce NTP on fleet; consider server-side ordering rules |
| A2 | Out-of-order / duplicate delivery | Optional `event_id` → idempotent ingest; out-of-order guard for vehicle state | Yes — model + service + route + migration | Yes (ADR §2.2, README payload notes) | Make `event_id` mandatory; consider monotonic per-vehicle sequence |
| A3 | Mission model is too minimal | Keep minimal; the spec only tests cancellation | No code change | Yes (ADR §2.3) | Add route/waypoints/payload/SLA/assignment history |
| A4 | Fault origin (telemetry vs. operator) | Both paths emit `FAULT_STATUS` uniformly | Yes — `services/vehicles.update_status` + route | Yes (ADR §2.4) | None — behaviour now consistent |
| A5 | Fault with no active mission | Always create maintenance record; cancellation no-op | Already correct (verified) | Yes (ADR §2.5) | None |
| A6 | `STALE_TELEMETRY` for never-seen vehicles | Suppress in demo to avoid noise | No code change | Yes (ADR §2.6) | Startup grace period then mark stale |
| A7 | Ingest rate / backpressure | Synchronous + pool-based backpressure for the stated scale | No code change | Yes (ADR §2.10, no throughput numbers) | Durable log + async side effects |
| A8 | Anomaly re-emission cooldown | Keep transition-based emission | No code change | Yes (ADR §2.7) | Add per-(vehicle,kind) cooldown window |
| A9 | Isolation level | `READ COMMITTED` + explicit `FOR UPDATE` | Already correct | Yes (ADR §1.4) | None |
| A10 | Batch POST | Single event per request | No code change | Yes (ADR §2.11) | `POST /telemetry/batch` |
| A11 | Vehicle status enum | Restricted to the four spec values | Already correct | Yes (ADR §2.8) | None |
| A12 | Initial fleet membership | Seeded `v-1`…`v-50`; unknown IDs rejected | Already correct (verified) | Yes (ADR §2.9) | Load from fleet registry |
| A13 | Auth | Out of scope | No code change | Yes (ADR §4) | Per-endpoint authorization |
| A14 | Pagination | `/anomalies` and `/telemetry` only | Already correct | Yes (ADR §4) | Paginate `/vehicles` at scale |
| A15 | Field validation | Pydantic + PG `CHECK` constraints | Already correct | Yes (ADR §2.12) | None |
| A16 | Historical retention | Indefinite | No code change | Yes (ADR §4) | Partition by time, purge policy |
| A17 | WS broadcast failure recovery | Re-seed via HTTP on every WS reconnect | Yes — `useWebSocket` + `useFleetStore` | Yes (ADR §1.3, README checklist) | None — pattern is the standard recovery |

---

## Reflection

- **The AI was good at:** scaffolding and boilerplate (FastAPI app, Dockerfile, migrations, Pydantic schemas, Tailwind components) — these came out essentially right on the first pass. It was also good at articulating trade-offs in the planning phase when given specific options to compare, and at translating user preferences ("do it the right way, not the fast way") into concrete architectural moves (Postgres, denormalized counter, WS over polling).
- **Where it failed me:** small typing / API-shape bugs that the type checker did not catch — the duplicate-key reducer case in the store would have shipped silently if the file had not been read back. It also showed a tendency to over-optimize (the `__import__("json").dumps` batch insert for a row count of usually 0 or 1) and produced one unused import in another file. A reviewer should not trust "looks done" without spot-checking the obvious places.
- **What I had to double-check manually:** the concurrency invariants. The 50-vehicle burst → exactly 50 increments is the spec's single most important test, and the only way to know it really holds is to run the code. The AI's reasoning about row-level locking on `zone_counts` is correct — but the demo confirmed it under real contention rather than on paper. Similarly for the fault path: I checked the four side-effects (vehicle status, mission status, mission `cancelled_reason`, maintenance record linkage) via `psql`, not just the HTTP response.
- **Where the spec was genuinely open and the AI helped reduce it to a decision:** the anomaly definition (six rules, transition-based emission), the mission lifecycle (endpoint-created, partial unique index), and the simulator delivery (embedded admin endpoints, with a documented fallback to a standalone script if it had blown the time budget — it did not).
- **Honest limit:** if the budget had been four hours instead of six, the WebSocket layer would have been the first thing to fall back to polling, and the simulator would have become a CLI script. The discipline points listed in the plan ("if X takes more than N minutes, switch to Y") were a real input to keeping the scope honest, not theatre.

