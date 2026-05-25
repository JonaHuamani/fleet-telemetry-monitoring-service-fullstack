-- Adds explicit server-side ingest timestamps and an optional client-supplied
-- event identifier for idempotent ingest.
--
-- Rationale:
--   * The vehicle-reported `ts` may be stale, replayed, or clock-skewed.
--     Persisting a separate `ingested_at` keeps the operational audit trail
--     independent of any client-side clock guarantees.
--   * Without a stable per-event identifier the ingest path cannot be made
--     idempotent under retries: a duplicate POST would otherwise be inserted
--     twice and would double-count zone entries.

ALTER TABLE telemetry
    ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE telemetry
    ADD COLUMN IF NOT EXISTS event_id UUID;

CREATE UNIQUE INDEX IF NOT EXISTS telemetry_event_id_unique
    ON telemetry (event_id)
    WHERE event_id IS NOT NULL;

ALTER TABLE anomalies
    ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
