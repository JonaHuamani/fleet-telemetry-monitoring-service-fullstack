from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.db import pool
from app.models import FleetState, StatusUpdateIn
from app.services.vehicles import (
    UnknownVehicle,
    fleet_state,
    list_vehicles,
    update_status,
)
from app.ws import broadcaster

router = APIRouter(tags=["vehicles"])


@router.get("/vehicles")
async def get_vehicles() -> list[dict[str, Any]]:
    return await list_vehicles(pool())


@router.get("/fleet/state", response_model=FleetState)
async def get_fleet_state() -> FleetState:
    s = await fleet_state(pool())
    return FleetState(**s)


@router.post("/vehicles/{vehicle_id}/status")
async def post_status(vehicle_id: str, payload: StatusUpdateIn) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    try:
        result = await update_status(
            pool(),
            vehicle_id=vehicle_id,
            new_status=payload.status,
            reason=payload.reason,
            now=now,
        )
    except UnknownVehicle:
        raise HTTPException(status_code=404, detail=f"unknown vehicle {vehicle_id}")

    await broadcaster.broadcast(
        {
            "type": "vehicle_update",
            "payload": {
                "id": vehicle_id,
                "status": payload.status,
                "last_seen_at": now.isoformat(),
            },
        }
    )
    if result["became_fault"]:
        await broadcaster.broadcast(
            {
                "type": "mission_update",
                "payload": {
                    "vehicle_id": vehicle_id,
                    "cancelled_mission_id": result["cancelled_mission_id"],
                    "maintenance_record_id": result["maintenance_record_id"],
                },
            }
        )
        if result.get("anomaly_ids"):
            await broadcaster.broadcast(
                {
                    "type": "anomaly",
                    "payload": {
                        "vehicle_id": vehicle_id,
                        "ts": now.isoformat(),
                        "kind": "FAULT_STATUS",
                        "severity": "critical",
                        "ids": result["anomaly_ids"],
                    },
                }
            )
    return result
