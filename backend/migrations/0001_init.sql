CREATE TABLE IF NOT EXISTS vehicles (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL CHECK (status IN ('idle','moving','charging','fault')) DEFAULT 'idle',
    battery_pct  INT  CHECK (battery_pct BETWEEN 0 AND 100),
    last_seen_at TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS telemetry (
    id           BIGSERIAL PRIMARY KEY,
    vehicle_id   TEXT NOT NULL REFERENCES vehicles(id),
    ts           TIMESTAMPTZ NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    battery_pct  INT NOT NULL CHECK (battery_pct BETWEEN 0 AND 100),
    speed_mps    DOUBLE PRECISION NOT NULL CHECK (speed_mps >= 0),
    status       TEXT NOT NULL CHECK (status IN ('idle','moving','charging','fault')),
    error_codes  JSONB NOT NULL DEFAULT '[]'::jsonb,
    zone_entered TEXT
);

CREATE INDEX IF NOT EXISTS telemetry_vehicle_ts_idx ON telemetry (vehicle_id, ts DESC);
CREATE INDEX IF NOT EXISTS telemetry_zone_entered_idx ON telemetry (zone_entered) WHERE zone_entered IS NOT NULL;

CREATE TABLE IF NOT EXISTS zone_counts (
    zone_id     TEXT PRIMARY KEY,
    entry_count INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS missions (
    id               BIGSERIAL PRIMARY KEY,
    vehicle_id       TEXT NOT NULL REFERENCES vehicles(id),
    status           TEXT NOT NULL CHECK (status IN ('active','completed','cancelled')) DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at     TIMESTAMPTZ,
    cancelled_reason TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS missions_one_active_per_vehicle
    ON missions (vehicle_id) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS missions_vehicle_idx ON missions (vehicle_id);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id         BIGSERIAL PRIMARY KEY,
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    mission_id BIGINT REFERENCES missions(id),
    opened_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS maintenance_vehicle_idx ON maintenance_records (vehicle_id);

CREATE TABLE IF NOT EXISTS anomalies (
    id         BIGSERIAL PRIMARY KEY,
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    details    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS anomalies_vehicle_ts_idx ON anomalies (vehicle_id, ts DESC);
CREATE INDEX IF NOT EXISTS anomalies_ts_idx ON anomalies (ts DESC);

-- Seed vehicles v-1 .. v-50
INSERT INTO vehicles (id, status, battery_pct)
SELECT 'v-' || g, 'idle', 100
FROM generate_series(1, 50) AS g
ON CONFLICT (id) DO NOTHING;

-- Seed zone_counts for the 20 named zones
INSERT INTO zone_counts (zone_id, entry_count)
VALUES
    ('inbound_dock_a', 0),
    ('inbound_dock_b', 0),
    ('receiving_staging', 0),
    ('aisle_a', 0),
    ('aisle_b', 0),
    ('aisle_c', 0),
    ('high_bay_1', 0),
    ('high_bay_2', 0),
    ('bulk_storage', 0),
    ('pick_zone_1', 0),
    ('pick_zone_2', 0),
    ('pack_station', 0),
    ('sort_belt', 0),
    ('outbound_dock_a', 0),
    ('outbound_dock_b', 0),
    ('shipping_staging', 0),
    ('charging_bay_1', 0),
    ('charging_bay_2', 0),
    ('charging_bay_3', 0),
    ('maintenance_bay', 0)
ON CONFLICT (zone_id) DO NOTHING;
