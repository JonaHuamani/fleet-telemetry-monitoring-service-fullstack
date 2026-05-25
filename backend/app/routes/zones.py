from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.db import pool
from app.services.vehicles import zone_counts

router = APIRouter(tags=["zones"])


@router.get("/zones/counts")
async def get_zone_counts() -> list[dict[str, Any]]:
    return await zone_counts(pool())
