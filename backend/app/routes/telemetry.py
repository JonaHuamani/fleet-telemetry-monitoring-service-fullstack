from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db import pool
from app.models import TelemetryAck, TelemetryIn
from app.services.telemetry import (
    UnknownVehicle,
    UnknownZone,
    fetch_anomalies,
    ingest,
)
from app.ws import broadcaster

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", response_model=TelemetryAck)
async def post_telemetry(event: TelemetryIn) -> TelemetryAck:
    try:
        result = await ingest(pool(), event)
    except UnknownVehicle:
        raise HTTPException(status_code=404, detail=f"unknown vehicle {event.vehicle_id}")
    except UnknownZone as e:
        raise HTTPException(status_code=400, detail=f"unknown zone {e!s}")

    if result.idempotent:
        # Duplicate event_id — same telemetry row already accepted and broadcast.
        return TelemetryAck(
            id=result.telemetry_id,
            anomalies_emitted=0,
            idempotent=True,
        )
    if result.applied_to_state:
        await broadcaster.broadcast(
            {
                "type": "vehicle_update",
                "payload": {
                    "id": event.vehicle_id,
                    "status": event.status,
                    "battery_pct": event.battery_pct,
                    "last_seen_at": event.timestamp.isoformat(),
                },
            }
        )
    if event.zone_entered is not None and result.new_zone_count is not None:
        await broadcaster.broadcast(
            {
                "type": "zone_count_update",
                "payload": {
                    "zone_id": event.zone_entered,
                    "entry_count": result.new_zone_count,
                },
            }
        )
    if result.became_fault:
        await broadcaster.broadcast(
            {
                "type": "mission_update",
                "payload": {
                    "vehicle_id": event.vehicle_id,
                    "cancelled_mission_id": result.cancelled_mission_id,
                    "maintenance_record_id": result.maintenance_record_id,
                },
            }
        )
    if result.anomaly_ids:
        await broadcaster.broadcast(
            {
                "type": "anomaly",
                "payload": {
                    "vehicle_id": event.vehicle_id,
                    "ts": event.timestamp.isoformat(),
                    "ids": result.anomaly_ids,
                },
            }
        )
    return TelemetryAck(
        id=result.telemetry_id,
        anomalies_emitted=len(result.anomaly_ids),
        idempotent=False,
    )


@router.get("/anomalies")
async def get_anomalies(
    vehicle_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return await fetch_anomalies(
        pool(),
        vehicle_id=vehicle_id,
        since=since,
        until=until,
        limit=limit,
    )
