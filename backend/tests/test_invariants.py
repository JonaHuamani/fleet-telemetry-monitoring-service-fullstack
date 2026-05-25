"""High-value invariant tests for the Qualitara backend.

Each test exercises one property the spec calls out (or that an honest
production deployment would care about):

  1. Concurrent zone-entry events never lose increments.
  2. Repeated `fault` updates do not create duplicate maintenance records.
  3. Older telemetry is persisted but never overwrites current vehicle state.
  4. `/anomalies` supports filtering by `vehicle_id`, time range, and `limit`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


def _telemetry_payload(
    vehicle_id: str,
    ts: datetime,
    *,
    status: str = "moving",
    battery_pct: int = 90,
    speed_mps: float = 1.0,
    zone_entered: str | None = None,
    error_codes: list[str] | None = None,
) -> dict:
    return {
        "vehicle_id": vehicle_id,
        "timestamp": ts.isoformat(),
        "lat": 0.0,
        "lon": 0.0,
        "battery_pct": battery_pct,
        "speed_mps": speed_mps,
        "status": status,
        "error_codes": error_codes or [],
        "zone_entered": zone_entered,
    }


async def test_concurrent_zone_increments_no_loss(client):
    """N concurrent zone-entry events must increase the counter by exactly N."""
    N = 20
    zone = "charging_bay_1"
    base_ts = datetime.now(tz=timezone.utc)

    async def fire(i: int):
        return await client.post(
            "/telemetry",
            json=_telemetry_payload(
                vehicle_id=f"v-{i + 1}",
                ts=base_ts + timedelta(milliseconds=i),
                zone_entered=zone,
            ),
        )

    results = await asyncio.gather(*(fire(i) for i in range(N)))
    failures = [r.text for r in results if r.status_code != 200]
    assert not failures, f"unexpected failures: {failures}"

    resp = await client.get("/zones/counts")
    counts = {z["zone_id"]: z["entry_count"] for z in resp.json()}
    assert counts[zone] == N, f"expected {N}, got {counts[zone]}"


async def test_repeated_fault_does_not_duplicate_maintenance(client, db_pool):
    """A second `fault` for an already-faulted vehicle is a no-op for maintenance."""
    vehicle = "v-1"

    # An active mission to be cancelled by the first fault.
    r = await client.post("/missions", json={"vehicle_id": vehicle})
    assert r.status_code == 200, r.text

    # First fault: real transition; should cancel the mission and open a record.
    r1 = await client.post(
        f"/vehicles/{vehicle}/status",
        json={"status": "fault", "reason": "first"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["became_fault"] is True
    assert r1.json()["cancelled_mission_id"] is not None
    assert r1.json()["maintenance_record_id"] is not None

    # Second fault: vehicle is already faulted; no transition, no new record.
    r2 = await client.post(
        f"/vehicles/{vehicle}/status",
        json={"status": "fault", "reason": "second"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["became_fault"] is False
    assert r2.json()["maintenance_record_id"] is None

    async with db_pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM maintenance_records WHERE vehicle_id = $1",
            vehicle,
        )
    assert n == 1, f"expected 1 maintenance record, got {n}"


async def test_older_telemetry_does_not_overwrite_state(client, db_pool):
    """Out-of-order events are persisted but cannot roll vehicle state backwards."""
    vehicle = "v-1"
    now = datetime.now(tz=timezone.utc)

    # Newer event lands first and establishes current state.
    r1 = await client.post(
        "/telemetry",
        json=_telemetry_payload(
            vehicle_id=vehicle,
            ts=now,
            status="moving",
            battery_pct=50,
            speed_mps=2.0,
        ),
    )
    assert r1.status_code == 200, r1.text

    # Older event arrives late: must be stored, but must NOT mutate vehicle state.
    older = now - timedelta(minutes=5)
    r2 = await client.post(
        "/telemetry",
        json=_telemetry_payload(
            vehicle_id=vehicle,
            ts=older,
            status="idle",
            battery_pct=10,
            speed_mps=0.0,
        ),
    )
    assert r2.status_code == 200, r2.text

    # Vehicle state reflects the newer event.
    vehicles = (await client.get("/vehicles")).json()
    v1 = next(v for v in vehicles if v["id"] == vehicle)
    assert v1["status"] == "moving"
    assert v1["battery_pct"] == 50

    # Both telemetry rows persisted (immutable history).
    async with db_pool.acquire() as conn:
        n_tel = await conn.fetchval(
            "SELECT COUNT(*) FROM telemetry WHERE vehicle_id = $1", vehicle
        )
    assert n_tel == 2, f"expected 2 telemetry rows, got {n_tel}"


async def test_anomalies_filter_by_vehicle_and_time_range(client, db_pool):
    """/anomalies supports vehicle_id, since, until, and limit."""
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(minutes=10)

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO anomalies (vehicle_id, ts, kind, severity, details)
            VALUES
              ('v-1', $1, 'LOW_BATTERY', 'warning', '{}'::jsonb),
              ('v-1', $2, 'OVERSPEED',  'warning', '{}'::jsonb),
              ('v-2', $1, 'LOW_BATTERY', 'warning', '{}'::jsonb)
            """,
            old,
            now,
        )

    # vehicle_id filter
    rows = (await client.get("/anomalies", params={"vehicle_id": "v-1"})).json()
    assert len(rows) == 2
    assert {r["vehicle_id"] for r in rows} == {"v-1"}

    # since filter — only the newer v-1 anomaly
    since = (now - timedelta(minutes=1)).isoformat()
    rows = (
        await client.get(
            "/anomalies", params={"vehicle_id": "v-1", "since": since}
        )
    ).json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "OVERSPEED"

    # until filter — only the older v-1 anomaly
    until = (now - timedelta(minutes=1)).isoformat()
    rows = (
        await client.get(
            "/anomalies", params={"vehicle_id": "v-1", "until": until}
        )
    ).json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "LOW_BATTERY"

    # limit caps the result set
    rows = (await client.get("/anomalies", params={"limit": 2})).json()
    assert len(rows) <= 2
