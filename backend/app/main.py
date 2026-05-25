from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.constants import STALE_SCAN_INTERVAL_SEC, STALE_TELEMETRY_SEC
from app.db import close_pool, init_pool, pool, run_migrations
from app.routes import admin, missions, stream, telemetry, vehicles, zones
from app.services.vehicles import detect_stale_vehicles
from app.ws import broadcaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")


async def _stale_loop() -> None:
    log.info("stale-telemetry loop started (interval=%ds, threshold=%ds)",
             STALE_SCAN_INTERVAL_SEC, STALE_TELEMETRY_SEC)
    try:
        while True:
            await asyncio.sleep(STALE_SCAN_INTERVAL_SEC)
            try:
                now = datetime.now(tz=timezone.utc)
                emitted = await detect_stale_vehicles(
                    pool(),
                    threshold_seconds=STALE_TELEMETRY_SEC,
                    now=now,
                )
                for a in emitted:
                    await broadcaster.broadcast(
                        {
                            "type": "anomaly",
                            "payload": {
                                "vehicle_id": a["vehicle_id"],
                                "kind": "STALE_TELEMETRY",
                                "severity": "warning",
                                "ts": a["ts"].isoformat() if hasattr(a["ts"], "isoformat") else a["ts"],
                            },
                        }
                    )
            except Exception:
                log.exception("stale-telemetry scan failed")
    except asyncio.CancelledError:
        pass
    finally:
        log.info("stale-telemetry loop stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await run_migrations()
    stale_task = asyncio.create_task(_stale_loop())
    log.info("application startup complete")
    try:
        yield
    finally:
        stale_task.cancel()
        try:
            await stale_task
        except asyncio.CancelledError:
            pass
        await close_pool()
        log.info("application shutdown complete")


app = FastAPI(title="Qualitara Fleet Telemetry", lifespan=lifespan)

cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry.router)
app.include_router(vehicles.router)
app.include_router(missions.router)
app.include_router(zones.router)
app.include_router(admin.router)
app.include_router(stream.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
