# AI Interaction Log

This file records the meaningful interactions I had with AI tools while building the Qualitara fullstack take-home, the corrections I made when the AI went the wrong way, the things I verified manually, and a short final reflection.

**Primary AI tool:** Claude Code, using the highest-context Claude model available in my plan at the time, running in my local terminal.
**Working language:** I conversed with the AI in Spanish; this log is kept in English because the deliverable is for an English-speaking reviewer. Prompts are translated/paraphrased faithfully.

---

## Session 1 — Project intake and planning

### Prompt 1 — "Can you fetch this Notion page?"
I pointed Claude Code at the take-home spec hosted on a public Notion page and asked whether it could read it.

**What Claude proposed:** It attempted a `WebFetch`. Notion renders content client-side via JavaScript, so the fetched HTML only contained the literal string `Notion` with no body. Claude reported this back and offered three workarounds: I could paste the content directly, export from Notion as Markdown/PDF, or share a static HTML mirror.

**My action:** I pasted the full spec into the chat.

---

### Prompt 2 — Full spec pasted into chat
I pasted the complete take-home prompt: build a fleet telemetry monitoring service (Python backend + React/TS dashboard + ADR + AI log), with detailed requirements for telemetry ingestion, anomaly detection, zone-traversal counting, atomic fault transitions, aggregate fleet state, and a 5–6 hour budget. The `ZONES` constant (20 zones) was included.

**What Claude proposed:** A structured summary of the four deliverables and three open questions before any code:
1. The AI log itself is a deliverable, so we should write it as we go rather than reconstruct it at the end.
2. How did I want to collaborate — full architectural plan up front, tight iterative loop, or targeted assistance while I drove?
3. Stack preference; Claude floated FastAPI + SQLite (WAL) + WebSockets as a plausible default but offered to discuss tradeoffs.

**My answers, setting the working rules for the session:**
- Keep the AI log in English even though we converse in Spanish.
- Architecture-first: nail down decisions and definitions before writing code.
- Discuss stack alternatives with pros/cons before committing.

---

## Session 2 — Stack discussion

### Prompt 3 — "Discuss the stack first; evaluate alternatives with pros and cons"
I asked Claude to compare alternatives across the whole stack before any implementation.

**What Claude proposed:** A structured comparison across five layers — backend framework (FastAPI vs Django REST), database (SQLite + WAL vs Postgres), zone-counting strategy (derived vs denormalized), dashboard transport (polling vs WebSockets), and frontend stack (Vite + React + TS + Tailwind). For each, pros, cons, and an initial recommendation. Claude's defaults: FastAPI, SQLite + WAL, derived counts, polling at 1 s, Vite/React/TS/Tailwind.

**My pushback:** I disagreed with three of Claude's five recommendations and pushed for the production-realistic choice in each case:
- **Postgres over SQLite** — "the right decision, not the fast one — tech debt has a cost for the client."
- **Denormalized zone counter over derived** — same reasoning. Counters should be O(1) at read time even if telemetry grows.
- **WebSockets over polling** — "1 s polling under burst events can mislead the user."

Claude accepted all three with refinements I agreed with:
- Postgres: add Docker Compose so the reviewer still has a one-command setup.
- Denormalized counter: keep telemetry as the immutable source of truth and update both tables in the same transaction; the counter is for O(1) reads, not a replacement for the audit trail.
- WebSockets: stay disciplined — server-push only, no presence/auth, basic reconnect, with an HTTP seed on connect.

Claude also surfaced three decisions I had not yet made (anomaly definition, mission lifecycle, telemetry generator approach) and listed concrete options for each.

---

### Prompt 4 — Decisions on the three open items
I chose:
- **Anomaly: A2** — threshold + pattern hybrid, to demonstrate business understanding.
- **Mission: B2** — `POST /missions` + `GET /missions`, minimal create/list, no full CRUD.
- **Telemetry generator: C2** — embedded admin endpoints, with willingness to fall back to a standalone script if it cost too much time.

**What Claude produced:** A finalized implementation plan covering the data model, endpoints, the `POST /telemetry` transaction shape, the `fault` path, frontend architecture, a verification plan, the list of files to create, and an explicit out-of-scope list for the ADR. Claude also flagged that A2 + B2 + C2 placed the project at the upper end of the 5–6 h budget and listed the discipline points that would keep it from overflowing (no component library, ~50-line WS manager, embedded simulator capped at 45 min net before fallback, tests deferred until the happy path is verified end-to-end).

I approved the plan without edits.

---

## Session 3 — Implementation

### Prompt 5 — "Plan approved, start coding"
I told Claude to begin implementation.

**What Claude produced:** A 1:1 task list mapping to the plan's phases (scaffold → data layer → telemetry ingest → missions/fault → reads → WS + stale loop → simulator → frontend scaffold → frontend components → e2e verification → docs). It worked through the list in this order:

1. **Scaffold** — `docker-compose.yml` (postgres 16-alpine + backend), `backend/Dockerfile` (python:3.12-slim, uvicorn), `requirements.txt` (FastAPI 0.115, asyncpg 0.30, pydantic 2.10), `.gitignore`, package layout.
2. **Constants and models** — `app/constants.py` (ZONES verbatim from spec, anomaly thresholds, vehicle IDs), `app/models.py` (Pydantic schemas).
3. **Migrations** — `migrations/0001_init.sql`: six tables, all indexes (including the partial unique index that enforces "at most one active mission per vehicle"), seeds for 50 vehicles and 20 zone-count rows. `db.py` runs `*.sql` files in lexicographic order and records applied ones in `_migrations`.
4. **WS broadcaster** — `app/ws.py`, ~40 lines: an `asyncio.Lock`-guarded set of connections, `connect`/`disconnect`/`broadcast`, dead-connection sweep on send failure.
5. **Anomaly rules** — `app/services/anomalies.py`: pure `evaluate_synchronous_rules(...)` returning `AnomalyToEmit` dataclasses (no DB I/O), then `insert_anomalies(conn, ...)` writes them. Transition-based emission for `LOW_BATTERY`, `FAULT_STATUS`, `ERROR_CODES_PRESENT`, `OVERSPEED`; per-event for `RAPID_BATTERY_DROP`.
6. **Mission service** — `app/services/missions.py`: `create_mission` (catches `UniqueViolationError` → `MissionConflict`), `cancel_active_mission_and_open_maintenance` (called from within an open transaction that already holds `FOR UPDATE` on the vehicle row; takes `FOR UPDATE` on the active mission row, then cancels it and inserts a maintenance record).
7. **Telemetry ingest** — `app/services/telemetry.py`: the heart of the system. Single transaction at `READ COMMITTED` with explicit row locks; one `SELECT … FOR UPDATE` on the vehicle row to serialize per-vehicle transition detection; reads the previous telemetry row for the comparisons that need `prev_speed_mps` and `prev_error_codes`; inserts telemetry; bumps `zone_counts` with a row-level lock if `zone_entered` is set; evaluates anomalies; updates the vehicle row; if status crossed into `fault`, runs the fault path inline.
8. **Vehicles service** — `app/services/vehicles.py`: explicit `update_status`, `list_vehicles` (with a `LEFT JOIN LATERAL` for each vehicle's latest anomaly), `fleet_state` (single grouped SELECT), `zone_counts`, and `detect_stale_vehicles` (the background scan that emits `STALE_TELEMETRY` anomalies with a re-emission throttle).
9. **Simulator** — `app/services/simulator.py`: a per-vehicle random-walk state machine, a 1 Hz loop using `asyncio.gather` for genuine concurrency at the DB layer, and a `burst()` function that fires N concurrent zone-entry events at the same zone.
10. **Routes** — one file per resource under `app/routes/`. Glued broadcasts to service results.
11. **`main.py`** — FastAPI lifespan that initializes the pool, runs migrations, spawns the stale-telemetry loop, and tears everything down cleanly on shutdown.

**Manual verification I ran against the live backend** (using `curl` and `psql` against `docker compose up`):

- Single telemetry POST returned `{"id":1,"anomalies_emitted":0}`. Good.
- Telemetry POST with `zone_entered` returned `{"id":2}` and `zone_counts.charging_bay_1` went from 0 to 1.
- Telemetry POST with `battery_pct: 10` returned `{"id":3,"anomalies_emitted":1}` and `GET /anomalies` showed the `LOW_BATTERY` warning.
- Mission creation + fault transition: `{"became_fault":true,"cancelled_mission_id":1,"maintenance_record_id":1}`; `psql` confirmed the mission flipped to `cancelled` and the maintenance record was linked.
- Burst of 20 vehicles into the same zone: `zone_counts` incremented by exactly 20.
- 5 concurrent bursts of 10 each into the same zone (50 total, fired with `&` in shell): `zone_counts` incremented by exactly 50. **This is the core spec invariant and I verified it manually before trusting the design.**

**Corrections I had to make to Claude's output during implementation:**

- Claude wrote `[__import__("json").dumps(...)]` in `insert_anomalies` as a quick hack to do a batch insert with `UNNEST`. I rejected it as ugly and asked for a small `for` loop of single inserts — anomaly emission per event is 0–1 rows in practice, so the batch was premature optimization.
- Claude added an unused `import json` to `missions.py`; I removed it on review.
- Claude initially produced a buggy reducer case in `frontend/src/store.ts` for `zone_count_update` that contained duplicate object keys. I caught it on a read-back and fixed it.
- The first `docker compose up` failed with "Bind for 0.0.0.0:5432 failed: port is already allocated" — a Postgres was already listening on the host. I remapped to `5433:5432` in `docker-compose.yml` and re-ran.

### Prompt 6 — "Now the frontend"
I asked Claude to scaffold the dashboard.

**What Claude produced:** Vite + React 18 + TS + Tailwind. `api.ts` is a 30-line typed `fetch` wrapper. `store.ts` is a single `useReducer` with one action per WS message kind plus a `seed` action fed by `Promise.all` on the three HTTP GETs. `useWebSocket.ts` uses `wss?://${host}/ws/stream` (proxied by Vite) with exponential-backoff reconnect capped at 10 s. Three components: `FleetSummary` (5 tiles), `VehicleTable` (50 rows with status badge, battery bar, last-seen tick, latest anomaly), `ZoneCounts` (20 cards). The header has three buttons to drive the simulator from the dashboard.

**My verification:** I ran `npx tsc -b` (clean), confirmed Vite served on 5173, raw-checked that the WS proxy returned `HTTP/1.1 101 Switching Protocols`, and watched live `vehicle_update`, `zone_count_update`, `anomaly`, and `mission_update` payloads come through after clicking "Start sim".

### Prompt 7 — "Write the docs"
I asked Claude to draft the ADR, README, and this log.

**What Claude produced:** A one-page ADR covering the load-bearing decisions (PG over SQLite, hybrid zone counting, WebSockets), the points the spec left open with explicit assumptions, a scale-up section with four specific bottlenecks, and a declared out-of-scope list. The README was a flat run-through: prerequisites, two commands to start, a CLI demo, a verification checklist, the API surface, the project layout, and a reset recipe.

---

## Session 4 — Ambiguity audit and follow-up changes

### Prompt 8 — "Audit every place where the spec was ambiguous"
After the slice was running end-to-end, I asked Claude to do a self-audit of every place the spec was open and we had made a silent decision.

**What Claude produced:** A 17-item list (A1–A17) classified by impact: arguably architectural (timestamps, duplicate delivery, mission domain, fault origin, stale detection for never-seen vehicles, ingest backpressure), implementation-level (anomaly re-emission, isolation, batch POST, status enum, initial fleet), and surface-level (auth, pagination, field validation, retention, WS recovery). The audit was reviewer-facing: each item named what the spec said (or didn't), the chosen behavior, the alternatives, and a frank evaluation of whether the chosen path was the best one. A few items were called out as weak — most notably (A1) trusting client timestamps unconditionally, (A2) the lack of an `event_id` for idempotent retry, and (A4) the silent asymmetry between the telemetry-driven and operator-driven fault paths.

### Prompt 9 — "Address A1–A17 with minimal safe changes; document the rest"
I gave Claude an explicit response strategy per item: implement the small, safe fixes; document the rest as assumptions or production follow-ups; no Kafka, no batch ingestion, no auth, no big architectural changes; do not claim unmeasured throughput numbers in the ADR.

**Code changes I accepted:**

1. **A1 + A2 — new migration `0002_event_id_and_ingested_at.sql`:** `ingested_at TIMESTAMPTZ DEFAULT NOW()` added to `telemetry` and `anomalies`; optional `event_id UUID` added to `telemetry` with a partial unique index `WHERE event_id IS NOT NULL`. The existing migration runner picks it up at startup automatically.
2. **A2 — idempotent ingest:** `TelemetryIn` gained an optional `event_id: UUID | None`. `TelemetryAck` gained an `idempotent: bool` flag. The ingest service now catches `asyncpg.UniqueViolationError` on the telemetry insert, looks up the existing row by `event_id`, and returns `idempotent=True` with no side effects re-applied. The route short-circuits before any broadcast in that case.
3. **A2 — out-of-order guard:** `ingest()` now branches on `event.timestamp >= prev_last_seen` (or `prev_last_seen IS NULL`). If the event is older, the telemetry row is still inserted and the zone counter is still bumped (entries are an immutable per-event aggregate), but the vehicle row is not updated and anomaly evaluation is skipped. A new `applied_to_state` flag on `IngestResult` tells the route whether to suppress the `vehicle_update` broadcast.
4. **A4 — uniform `FAULT_STATUS` emission:** `services.vehicles.update_status` now inserts a `FAULT_STATUS` anomaly inside its transaction when `became_fault` is true. The route broadcasts it alongside the `mission_update`. Both fault paths now emit the same anomaly, removing the previous silent asymmetry.
5. **A17 — frontend re-seed on reconnect:** `useFleetStore` exposes a `reseed` callback; `useFleetWebSocket` accepts an `onReconnect` option and invokes it on every `ws.onopen` *after* the first disconnect. The first connect still uses the mount-time seed to avoid a double fetch. The dashboard now self-heals from a backend restart by re-fetching `/fleet/state`, `/vehicles`, and `/zones/counts` before applying further WS deltas.

**Documentation changes I accepted:**

- The ADR was reorganised into three sections: load-bearing decisions, points the spec leaves open with the explicit resolution this implementation chose, and an enumerated out-of-scope list. The wording for each open point is reviewer-facing — "the spec leaves this open; this implementation assumes…" rather than apologetic.
- The README's verification checklist gained four rows: repeated fault does not double-create maintenance records, older telemetry does not roll state backwards, duplicate `event_id` is idempotent, and the dashboard recovers from a dropped WebSocket. A short "Telemetry payload notes" subsection documents the `event_id`, `ingested_at`, and out-of-order semantics.

**What I deliberately did NOT do, and why:**

- **A6 startup grace period for never-seen vehicles** — documented as a known limitation rather than implemented. The current behaviour keeps demo startup quiet, which I prefer for the reviewer experience.
- **A8 anomaly cooldown window** — documented as a production follow-up. Implementing it cleanly requires either a `last_emitted_at` column per `(vehicle, kind)` or a window scan against `anomalies`; both are noticeable diffs for a behaviour the spec does not require.
- **Batch POST** (A10), **mission domain expansion** (A3), **auth** (A13), **retention/partitioning** (A16), and **historical view** were left in the ADR's out-of-scope list.

---

## Session 5 — Tests and final pass

### Prompt 10 — "Add the highest-value invariant tests"
I asked Claude to add a minimal pytest suite for the four invariants I most cared about.

**What Claude produced:** `backend/requirements-dev.txt` (pytest, pytest-asyncio, httpx), `backend/pytest.ini` (`asyncio_mode = auto`), `backend/tests/conftest.py` (initialises the asyncpg pool, runs migrations, truncates per test, exposes an httpx `AsyncClient` over ASGI), and `backend/tests/test_invariants.py` with four tests:

1. **Concurrent zone increments** — 20 `asyncio.gather` POSTs at the same zone; the counter must end at exactly 20.
2. **Repeated fault idempotency** — two `POST /vehicles/v-1/status` with `status=fault`; only the first must report `became_fault=true`, and only one `maintenance_records` row must exist for `v-1`.
3. **Out-of-order guard** — a newer event followed by an older event; vehicle state must reflect the newer one and both telemetry rows must be persisted.
4. **Anomalies filter** — `/anomalies` honors `vehicle_id`, `since`, `until`, and `limit`.

The backend `Dockerfile` was updated to also install `requirements-dev.txt` so tests can run with `docker compose exec backend pytest`. The README documents this.

**Manual verification:** I ran the AST parser over the new files and confirmed `pytest --collect-only` works inside the backend container. I also confirmed the four tests pass against the running stack.

### Prompt 11 — Final A1–A17 status table

| # | Topic | Decision | Implemented in code? | Documented? | Production follow-up |
|---|---|---|---|---|---|
| A1 | Timestamp source (vehicle vs server) | Store both: `ts` (event) and `ingested_at` (server) | Yes — migration 0002 + ADR §2.1 | Yes | Enforce NTP on fleet; server-side ordering rules |
| A2 | Duplicate delivery / idempotency | Optional `event_id` → idempotent ingest; out-of-order guard for vehicle state | Yes — model + service + route + migration | Yes (ADR §2.2, README payload notes) | Make `event_id` mandatory; consider monotonic per-vehicle sequence |
| A3 | Mission model is too minimal | Keep minimal; the spec only tests cancellation | No code change | Yes (ADR §2.3) | Add route/waypoints/payload/SLA/assignment history |
| A4 | Fault origin (telemetry vs operator) | Both paths emit `FAULT_STATUS` uniformly | Yes — `services/vehicles.update_status` + route | Yes (ADR §2.4) | None — behaviour now consistent |
| A5 | Fault with no active mission | Always create maintenance record; cancellation no-op | Already correct (verified + test) | Yes (ADR §2.5) | None |
| A6 | `STALE_TELEMETRY` for never-seen vehicles | Suppress in demo to avoid noise | No code change | Yes (ADR §2.6) | Startup grace period then mark stale |
| A7 | Ingest rate / backpressure | Synchronous + pool-based backpressure for the stated scale | No code change | Yes (ADR §2.10, no throughput numbers) | Durable log + async side effects |
| A8 | Anomaly re-emission cooldown | Keep transition-based emission | No code change | Yes (ADR §2.7) | Add per-`(vehicle, kind)` cooldown window |
| A9 | Isolation level | `READ COMMITTED` + explicit `FOR UPDATE` | Already correct | Yes (ADR §1.4) | None |
| A10 | Batch POST | Single event per request | No code change | Yes (ADR §2.11) | `POST /telemetry/batch` |
| A11 | Vehicle status enum | Restricted to the four spec values | Already correct | Yes (ADR §2.8) | None |
| A12 | Initial fleet membership | Seeded `v-1`…`v-50`; unknown IDs rejected (404) | Already correct (verified) | Yes (ADR §2.9) | Load from fleet registry |
| A13 | Auth | Out of scope | No code change | Yes (ADR §4) | Per-endpoint authorization |
| A14 | Pagination | `/anomalies` and `/telemetry` only | Already correct | Yes (ADR §4) | Paginate `/vehicles` at scale |
| A15 | Field validation | Pydantic + PG `CHECK` constraints | Already correct | Yes (ADR §2.12) | None |
| A16 | Historical retention | Indefinite | No code change | Yes (ADR §4) | Partition by time, purge policy |
| A17 | WebSocket recovery | Re-seed via HTTP on every WS reconnect | Yes — `useWebSocket` + `useFleetStore` | Yes (ADR §1.3, README checklist) | None — pattern is the standard recovery |

---

## Reflection

- **AI was useful for** scaffolding (FastAPI app, Dockerfile, migrations, Pydantic schemas, Tailwind components), boilerplate, exploring tradeoffs when I gave it specific options to compare, and drafting documentation in a reviewer-friendly tone. It accelerated me through the parts of the project that didn't need judgment.
- **AI was weak around** subtle correctness and concurrency unless I prompted it explicitly. It defaulted to SQLite, polling, and a derived zone counter — defensible for a demo, wrong for the spirit of this spec. It also produced small bugs that would have shipped silently (a duplicate-key reducer case in `store.ts`; an `__import__("json").dumps` "optimisation" for inserting at most one row per event). Type checkers and tests caught some of these, my read-back caught the rest.
- **I verified the critical invariants manually**: the 50-vehicle concurrent burst against the same zone counter (exactly 50, no losses), and the fault path's four side-effects via `psql` — vehicle status, mission status, mission `cancelled_reason`, maintenance record linkage. I did not trust the LLM's reasoning about row locking until the demo confirmed it under real contention.
- **The most important human decisions** were: Postgres over SQLite, atomic zone counting in the same transaction as telemetry, the transactional fault workflow with `FOR UPDATE` on the vehicle row, and documenting the limits of idempotency honestly rather than pretending exactly-once was solved. Those came from me, not the AI.
- **AI accelerated implementation but did not replace architecture review.** Every decision that mattered for correctness — the database, the isolation strategy, the broadcast pattern, the response to the ambiguity audit — was driven by me, with the AI either proposing options I evaluated or executing a plan I had already approved.
