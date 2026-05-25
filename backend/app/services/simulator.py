from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.constants import VEHICLE_IDS, ZONES
from app.models import TelemetryIn
from app.services.telemetry import ingest as telemetry_ingest
from app.ws import broadcaster

log = logging.getLogger(__name__)


class _SimState:
    def __init__(self) -> None:
        self.running: bool = False
        self.tick_hz: float = 1.0
        self.vehicle_count: int = 50
        self.task: asyncio.Task | None = None
        # Per-vehicle simulation state
        self.battery: dict[str, float] = {}
        self.lat: dict[str, float] = {}
        self.lon: dict[str, float] = {}
        self.status: dict[str, str] = {}
        self.zone_idx: dict[str, int] = {}
        self.error_codes_active: dict[str, bool] = {}


_state = _SimState()


def _seed_vehicle(vehicle_id: str) -> None:
    if vehicle_id in _state.battery:
        return
    _state.battery[vehicle_id] = random.uniform(60, 100)
    _state.lat[vehicle_id] = 37.41 + random.uniform(-0.005, 0.005)
    _state.lon[vehicle_id] = -122.08 + random.uniform(-0.005, 0.005)
    _state.status[vehicle_id] = "idle"
    _state.zone_idx[vehicle_id] = random.randint(0, len(ZONES) - 1)
    _state.error_codes_active[vehicle_id] = False


def _step_vehicle(vehicle_id: str) -> TelemetryIn:
    """Advance one vehicle by one tick and return a synthetic telemetry event."""
    _seed_vehicle(vehicle_id)
    status = _state.status[vehicle_id]

    # State machine: tiny chance of status flip per tick.
    roll = random.random()
    if status == "idle":
        if roll < 0.05:
            status = "moving"
        elif roll < 0.06:
            status = "charging"
    elif status == "moving":
        if roll < 0.005:
            status = "idle"
        elif _state.battery[vehicle_id] < 20 and roll < 0.5:
            status = "charging"
    elif status == "charging":
        if _state.battery[vehicle_id] > 90 and roll < 0.3:
            status = "idle"
    elif status == "fault":
        if roll < 0.002:
            status = "idle"

    # Battery walk
    bat = _state.battery[vehicle_id]
    if status == "moving":
        bat = max(0.0, bat - random.uniform(0.05, 0.4))
    elif status == "charging":
        bat = min(100.0, bat + random.uniform(0.5, 1.5))
    # else (idle/fault): no change

    # Speed
    if status == "moving":
        speed = random.uniform(0.5, 4.5)
    else:
        speed = 0.0

    # Position walk
    if status == "moving":
        _state.lat[vehicle_id] += random.uniform(-0.0002, 0.0002)
        _state.lon[vehicle_id] += random.uniform(-0.0002, 0.0002)

    # Zone transition (~5% per tick if moving)
    zone_entered: str | None = None
    if status == "moving" and random.random() < 0.05:
        new_zone_idx = (_state.zone_idx[vehicle_id] + random.choice([-1, 1, 2])) % len(ZONES)
        if new_zone_idx != _state.zone_idx[vehicle_id]:
            _state.zone_idx[vehicle_id] = new_zone_idx
            zone_entered = ZONES[new_zone_idx]

    # Error codes (rare)
    if not _state.error_codes_active[vehicle_id] and random.random() < 0.001:
        _state.error_codes_active[vehicle_id] = True
    error_codes = ["E_GENERIC"] if _state.error_codes_active[vehicle_id] else []
    if _state.error_codes_active[vehicle_id] and random.random() < 0.05:
        _state.error_codes_active[vehicle_id] = False

    _state.battery[vehicle_id] = bat
    _state.status[vehicle_id] = status

    return TelemetryIn(
        vehicle_id=vehicle_id,
        timestamp=datetime.now(tz=timezone.utc),
        lat=_state.lat[vehicle_id],
        lon=_state.lon[vehicle_id],
        battery_pct=int(round(bat)),
        speed_mps=speed,
        status=status,  # type: ignore[arg-type]
        error_codes=error_codes,
        zone_entered=zone_entered,
    )


async def _broadcast_for_result(event: TelemetryIn, result: Any) -> None:
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


async def _tick(pool: asyncpg.Pool) -> None:
    events = [_step_vehicle(VEHICLE_IDS[i]) for i in range(_state.vehicle_count)]

    async def _one(e: TelemetryIn) -> None:
        try:
            result = await telemetry_ingest(pool, e)
            await _broadcast_for_result(e, result)
        except Exception:
            log.exception("simulator ingest failed for %s", e.vehicle_id)

    await asyncio.gather(*(_one(e) for e in events))


async def _loop(pool: asyncpg.Pool) -> None:
    interval = 1.0 / _state.tick_hz
    log.info("simulator loop started: %.1f Hz, %d vehicles", _state.tick_hz, _state.vehicle_count)
    try:
        while _state.running:
            t0 = asyncio.get_event_loop().time()
            await _tick(pool)
            elapsed = asyncio.get_event_loop().time() - t0
            sleep_for = max(0.0, interval - elapsed)
            await asyncio.sleep(sleep_for)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("simulator loop stopped")


def start(pool: asyncpg.Pool, *, tick_hz: float, vehicle_count: int) -> bool:
    if _state.running:
        return False
    _state.running = True
    _state.tick_hz = tick_hz
    _state.vehicle_count = max(1, min(vehicle_count, len(VEHICLE_IDS)))
    _state.task = asyncio.create_task(_loop(pool))
    return True


async def stop() -> bool:
    if not _state.running:
        return False
    _state.running = False
    if _state.task is not None:
        _state.task.cancel()
        try:
            await _state.task
        except asyncio.CancelledError:
            pass
        _state.task = None
    return True


def status() -> dict[str, Any]:
    return {
        "running": _state.running,
        "tick_hz": _state.tick_hz if _state.running else None,
        "vehicle_count": _state.vehicle_count if _state.running else None,
    }


async def burst(
    pool: asyncpg.Pool,
    *,
    zone_id: str,
    vehicle_count: int,
    jitter_ms: int,
) -> dict[str, Any]:
    """Fire N concurrent telemetry events all entering the same zone.

    Used to exercise row-level locking on zone_counts under contention.
    """
    vehicle_ids = VEHICLE_IDS[: min(vehicle_count, len(VEHICLE_IDS))]
    now = datetime.now(tz=timezone.utc)

    async def _one(vehicle_id: str) -> int:
        if jitter_ms > 0:
            await asyncio.sleep(random.uniform(0, jitter_ms) / 1000.0)
        _seed_vehicle(vehicle_id)
        event = TelemetryIn(
            vehicle_id=vehicle_id,
            timestamp=now,
            lat=_state.lat[vehicle_id],
            lon=_state.lon[vehicle_id],
            battery_pct=int(round(_state.battery[vehicle_id])),
            speed_mps=1.0,
            status="moving",
            error_codes=[],
            zone_entered=zone_id,
        )
        result = await telemetry_ingest(pool, event)
        await _broadcast_for_result(event, result)
        return result.telemetry_id

    ids = await asyncio.gather(*(_one(vid) for vid in vehicle_ids))
    return {"zone_id": zone_id, "events_sent": len(ids), "telemetry_ids": ids}
