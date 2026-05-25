from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from app.constants import OVERSPEED_MPS, ZONES_SET
from app.models import TelemetryIn
from app.services.anomalies import (
    AnomalyToEmit,
    evaluate_synchronous_rules,
    insert_anomalies,
)
from app.services.missions import cancel_active_mission_and_open_maintenance

log = logging.getLogger(__name__)


class UnknownVehicle(Exception):
    pass


class UnknownZone(Exception):
    pass


@dataclass
class IngestResult:
    telemetry_id: int
    anomaly_ids: list[int]
    vehicle_status_changed: bool
    became_fault: bool
    cancelled_mission_id: int | None
    maintenance_record_id: int | None
    new_zone_count: int | None  # for zone broadcast, when zone_entered is set
    idempotent: bool = False    # event_id matched a previously accepted row
    applied_to_state: bool = True  # False if event was older than current state


async def ingest(
    pool: asyncpg.Pool,
    event: TelemetryIn,
) -> IngestResult:
    """Ingest one telemetry event in a single PG transaction.

    Steps (all under READ COMMITTED with explicit row locks):
      1. SELECT vehicle FOR UPDATE (serializes per-vehicle).
      2. SELECT previous telemetry (latest by ts) for transition detection.
      3. INSERT telemetry. UniqueViolation on event_id → idempotent return.
      4. If zone_entered set: UPDATE zone_counts (+1) — row-level lock per zone.
      5. If event is newer than current vehicle state: evaluate anomaly rules,
         insert anomalies, update vehicle row, run fault path on transition.
         Otherwise persist the telemetry row + zone increment only (out-of-order
         events are stored but cannot rewrite current state into the past).
      6. Commit.
    """
    if event.zone_entered is not None and event.zone_entered not in ZONES_SET:
        raise UnknownZone(event.zone_entered)

    async with pool.acquire() as conn:
        async with conn.transaction():
            vehicle = await conn.fetchrow(
                """
                SELECT id, status, battery_pct, last_seen_at
                FROM vehicles
                WHERE id = $1
                FOR UPDATE
                """,
                event.vehicle_id,
            )
            if vehicle is None:
                raise UnknownVehicle(event.vehicle_id)

            prev_status = vehicle["status"]
            prev_battery = vehicle["battery_pct"]
            prev_last_seen = vehicle["last_seen_at"]

            prev_tel = await conn.fetchrow(
                """
                SELECT speed_mps, error_codes, ts
                FROM telemetry
                WHERE vehicle_id = $1
                ORDER BY ts DESC, id DESC
                LIMIT 1
                """,
                event.vehicle_id,
            )
            prev_overspeed: bool | None = None
            prev_error_codes_empty: bool | None = None
            if prev_tel is not None:
                prev_overspeed = prev_tel["speed_mps"] > OVERSPEED_MPS
                prev_codes = prev_tel["error_codes"]
                if isinstance(prev_codes, str):
                    prev_codes = json.loads(prev_codes)
                prev_error_codes_empty = (not prev_codes) or len(prev_codes) == 0

            try:
                tel_row = await conn.fetchrow(
                    """
                    INSERT INTO telemetry
                      (vehicle_id, ts, lat, lon, battery_pct, speed_mps, status,
                       error_codes, zone_entered, event_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
                    RETURNING id
                    """,
                    event.vehicle_id,
                    event.timestamp,
                    event.lat,
                    event.lon,
                    event.battery_pct,
                    event.speed_mps,
                    event.status,
                    json.dumps(event.error_codes),
                    event.zone_entered,
                    event.event_id,
                )
            except asyncpg.UniqueViolationError:
                existing = await conn.fetchrow(
                    "SELECT id FROM telemetry WHERE event_id = $1",
                    event.event_id,
                )
                return IngestResult(
                    telemetry_id=existing["id"] if existing else 0,
                    anomaly_ids=[],
                    vehicle_status_changed=False,
                    became_fault=False,
                    cancelled_mission_id=None,
                    maintenance_record_id=None,
                    new_zone_count=None,
                    idempotent=True,
                    applied_to_state=False,
                )
            telemetry_id = tel_row["id"]

            new_zone_count: int | None = None
            if event.zone_entered is not None:
                row = await conn.fetchrow(
                    """
                    UPDATE zone_counts
                    SET entry_count = entry_count + 1, updated_at = NOW()
                    WHERE zone_id = $1
                    RETURNING entry_count
                    """,
                    event.zone_entered,
                )
                new_zone_count = row["entry_count"] if row else None

            # Out-of-order guard: if the incoming event predates the vehicle's
            # current last_seen_at, persist the telemetry row + zone counter
            # but do not roll vehicle state backwards or re-evaluate transitions
            # against stale context.
            is_newer = prev_last_seen is None or event.timestamp >= prev_last_seen
            if not is_newer:
                return IngestResult(
                    telemetry_id=telemetry_id,
                    anomaly_ids=[],
                    vehicle_status_changed=False,
                    became_fault=False,
                    cancelled_mission_id=None,
                    maintenance_record_id=None,
                    new_zone_count=new_zone_count,
                    idempotent=False,
                    applied_to_state=False,
                )

            to_emit: list[AnomalyToEmit] = evaluate_synchronous_rules(
                prev_status=prev_status,
                prev_battery_pct=prev_battery,
                prev_last_seen_at=prev_last_seen,
                prev_error_codes_empty=prev_error_codes_empty,
                prev_speed_overspeed=prev_overspeed,
                new_status=event.status,
                new_battery_pct=event.battery_pct,
                new_speed_mps=event.speed_mps,
                new_error_codes=event.error_codes,
                new_ts=event.timestamp,
            )
            anomaly_ids = await insert_anomalies(
                conn,
                vehicle_id=event.vehicle_id,
                ts=event.timestamp,
                anomalies=to_emit,
            )

            await conn.execute(
                """
                UPDATE vehicles
                SET status = $1, battery_pct = $2, last_seen_at = $3, updated_at = NOW()
                WHERE id = $4
                """,
                event.status,
                event.battery_pct,
                event.timestamp,
                event.vehicle_id,
            )

            became_fault = event.status == "fault" and prev_status != "fault"
            cancelled_mission_id: int | None = None
            maintenance_record_id: int | None = None
            if became_fault:
                cancelled_mission_id, maintenance_record_id = (
                    await cancel_active_mission_and_open_maintenance(
                        conn,
                        vehicle_id=event.vehicle_id,
                        reason="vehicle_fault_telemetry",
                        now=event.timestamp,
                    )
                )

            return IngestResult(
                telemetry_id=telemetry_id,
                anomaly_ids=anomaly_ids,
                vehicle_status_changed=prev_status != event.status,
                became_fault=became_fault,
                cancelled_mission_id=cancelled_mission_id,
                maintenance_record_id=maintenance_record_id,
                new_zone_count=new_zone_count,
                applied_to_state=True,
            )


async def fetch_anomalies(
    pool: asyncpg.Pool,
    *,
    vehicle_id: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if vehicle_id is not None:
        args.append(vehicle_id)
        clauses.append(f"vehicle_id = ${len(args)}")
    if since is not None:
        args.append(since)
        clauses.append(f"ts >= ${len(args)}")
    if until is not None:
        args.append(until)
        clauses.append(f"ts <= ${len(args)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, vehicle_id, ts, kind, severity, details
            FROM anomalies
            {where}
            ORDER BY ts DESC, id DESC
            LIMIT ${len(args)}
            """,
            *args,
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("details"), str):
            d["details"] = json.loads(d["details"])
        out.append(d)
    return out
