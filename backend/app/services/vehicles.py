from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

from app.services.anomalies import AnomalyToEmit, insert_anomalies
from app.services.missions import cancel_active_mission_and_open_maintenance


class UnknownVehicle(Exception):
    pass


async def update_status(
    pool: asyncpg.Pool,
    *,
    vehicle_id: str,
    new_status: str,
    reason: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Apply an explicit status update.

    When the new status is 'fault' and the previous status was not, the active
    mission (if any) is cancelled and a maintenance record is opened in the
    same transaction. Uses FOR UPDATE on the vehicle row to serialize racing
    status updates for the same vehicle.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            v = await conn.fetchrow(
                """
                SELECT id, status FROM vehicles WHERE id = $1 FOR UPDATE
                """,
                vehicle_id,
            )
            if v is None:
                raise UnknownVehicle(vehicle_id)
            prev_status = v["status"]
            await conn.execute(
                """
                UPDATE vehicles
                SET status = $1, updated_at = NOW()
                WHERE id = $2
                """,
                new_status,
                vehicle_id,
            )
            became_fault = new_status == "fault" and prev_status != "fault"
            cancelled_mission_id: int | None = None
            maintenance_record_id: int | None = None
            anomaly_ids: list[int] = []
            if became_fault:
                cancelled_mission_id, maintenance_record_id = (
                    await cancel_active_mission_and_open_maintenance(
                        conn,
                        vehicle_id=vehicle_id,
                        reason=reason or "vehicle_fault_status_update",
                        now=now,
                    )
                )
                # Emit FAULT_STATUS uniformly with the telemetry-driven path so
                # the anomaly feed reflects every real transition into fault
                # regardless of which endpoint triggered it.
                anomaly_ids = await insert_anomalies(
                    conn,
                    vehicle_id=vehicle_id,
                    ts=now,
                    anomalies=[
                        AnomalyToEmit(
                            kind="FAULT_STATUS",
                            severity="critical",
                            details={
                                "prev_status": prev_status,
                                "source": "status_update",
                            },
                        )
                    ],
                )
            return {
                "vehicle_id": vehicle_id,
                "prev_status": prev_status,
                "new_status": new_status,
                "became_fault": became_fault,
                "cancelled_mission_id": cancelled_mission_id,
                "maintenance_record_id": maintenance_record_id,
                "anomaly_ids": anomaly_ids,
            }


async def list_vehicles(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all vehicles with their latest anomaly (if any)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              v.id,
              v.status,
              v.battery_pct,
              v.last_seen_at,
              a.kind     AS latest_anomaly_kind,
              a.ts       AS latest_anomaly_ts,
              a.severity AS latest_anomaly_severity
            FROM vehicles v
            LEFT JOIN LATERAL (
              SELECT kind, ts, severity
              FROM anomalies
              WHERE vehicle_id = v.id
              ORDER BY ts DESC, id DESC
              LIMIT 1
            ) a ON TRUE
            ORDER BY
              CASE
                WHEN v.id ~ '^v-[0-9]+$' THEN (split_part(v.id, '-', 2))::int
                ELSE 999999
              END
            """
        )
    return [dict(r) for r in rows]


async def fleet_state(pool: asyncpg.Pool) -> dict[str, int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT status, COUNT(*)::int AS n
            FROM vehicles
            GROUP BY status
            """
        )
    out = {"idle": 0, "moving": 0, "charging": 0, "fault": 0}
    for r in rows:
        out[r["status"]] = r["n"]
    out["total"] = sum(out.values())
    return out


async def zone_counts(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT zone_id, entry_count, updated_at
            FROM zone_counts
            ORDER BY zone_id
            """
        )
    return [dict(r) for r in rows]


async def detect_stale_vehicles(
    pool: asyncpg.Pool,
    *,
    threshold_seconds: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Find vehicles silent for > threshold_seconds and emit STALE_TELEMETRY anomalies.

    Re-emission is rate-limited: a STALE_TELEMETRY for the same vehicle within
    the last threshold_seconds is treated as still-known and not re-emitted.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT v.id, v.last_seen_at
                FROM vehicles v
                WHERE v.last_seen_at IS NOT NULL
                  AND v.last_seen_at < NOW() - ($1 || ' seconds')::interval
                  AND NOT EXISTS (
                    SELECT 1 FROM anomalies a
                    WHERE a.vehicle_id = v.id
                      AND a.kind = 'STALE_TELEMETRY'
                      AND a.ts > NOW() - ($1 || ' seconds')::interval
                  )
                """,
                str(threshold_seconds),
            )
            emitted: list[dict[str, Any]] = []
            for r in rows:
                vehicle_id = r["id"]
                last_seen = r["last_seen_at"]
                details = {
                    "last_seen_at": last_seen.isoformat() if last_seen else None,
                    "threshold_sec": threshold_seconds,
                }
                anomaly = await conn.fetchrow(
                    """
                    INSERT INTO anomalies (vehicle_id, ts, kind, severity, details)
                    VALUES ($1, $2, 'STALE_TELEMETRY', 'warning', $3::jsonb)
                    RETURNING id, vehicle_id, ts, kind, severity, details
                    """,
                    vehicle_id,
                    now,
                    json.dumps(details),
                )
                d = dict(anomaly)
                if isinstance(d.get("details"), str):
                    d["details"] = json.loads(d["details"])
                emitted.append(d)
    return emitted
