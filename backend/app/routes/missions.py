from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db import pool
from app.models import MissionIn, MissionOut
from app.services.missions import (
    MissionConflict,
    UnknownVehicle,
    create_mission,
    list_missions,
)
from app.ws import broadcaster

router = APIRouter(tags=["missions"])


@router.post("/missions", response_model=MissionOut)
async def post_mission(payload: MissionIn) -> dict[str, Any]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            try:
                row = await create_mission(conn, payload.vehicle_id)
            except UnknownVehicle:
                raise HTTPException(
                    status_code=404, detail=f"unknown vehicle {payload.vehicle_id}"
                )
            except MissionConflict:
                raise HTTPException(
                    status_code=409,
                    detail=f"vehicle {payload.vehicle_id} already has an active mission",
                )
    await broadcaster.broadcast(
        {
            "type": "mission_update",
            "payload": {
                "vehicle_id": payload.vehicle_id,
                "active_mission_id": row["id"],
            },
        }
    )
    return row


@router.get("/missions")
async def get_missions(
    vehicle_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with pool().acquire() as conn:
        return await list_missions(
            conn, vehicle_id=vehicle_id, status=status, limit=limit
        )
