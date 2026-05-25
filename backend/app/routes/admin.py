from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.constants import ZONES_SET
from app.db import pool
from app.models import SimulatorBurstIn, SimulatorStartIn, SimulatorStatus
from app.services import simulator

router = APIRouter(prefix="/admin/simulator", tags=["admin"])


@router.post("/start")
async def post_start(payload: SimulatorStartIn) -> dict[str, Any]:
    started = simulator.start(
        pool(),
        tick_hz=payload.tick_hz,
        vehicle_count=payload.vehicle_count,
    )
    return {"started": started, **simulator.status()}


@router.post("/stop")
async def post_stop() -> dict[str, Any]:
    stopped = await simulator.stop()
    return {"stopped": stopped, **simulator.status()}


@router.get("/status", response_model=SimulatorStatus)
async def get_status() -> SimulatorStatus:
    return SimulatorStatus(**simulator.status())


@router.post("/burst")
async def post_burst(payload: SimulatorBurstIn) -> dict[str, Any]:
    if payload.zone_id not in ZONES_SET:
        raise HTTPException(status_code=400, detail=f"unknown zone {payload.zone_id}")
    return await simulator.burst(
        pool(),
        zone_id=payload.zone_id,
        vehicle_count=payload.vehicle_count,
        jitter_ms=payload.jitter_ms,
    )
