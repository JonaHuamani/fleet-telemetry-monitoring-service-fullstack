from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg


class MissionConflict(Exception):
    """A vehicle already has an active mission."""


class UnknownVehicle(Exception):
    pass


async def create_mission(conn: asyncpg.Connection, vehicle_id: str) -> dict[str, Any]:
    exists = await conn.fetchrow("SELECT id FROM vehicles WHERE id = $1", vehicle_id)
    if exists is None:
        raise UnknownVehicle(vehicle_id)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO missions (vehicle_id, status)
            VALUES ($1, 'active')
            RETURNING id, vehicle_id, status, created_at, cancelled_at, cancelled_reason
            """,
            vehicle_id,
        )
    except asyncpg.UniqueViolationError as e:
        raise MissionConflict(vehicle_id) from e
    return dict(row)


async def list_missions(
    conn: asyncpg.Connection,
    *,
    vehicle_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if vehicle_id is not None:
        args.append(vehicle_id)
        clauses.append(f"vehicle_id = ${len(args)}")
    if status is not None:
        args.append(status)
        clauses.append(f"status = ${len(args)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(limit)
    rows = await conn.fetch(
        f"""
        SELECT id, vehicle_id, status, created_at, cancelled_at, cancelled_reason
        FROM missions
        {where}
        ORDER BY id DESC
        LIMIT ${len(args)}
        """,
        *args,
    )
    return [dict(r) for r in rows]


async def cancel_active_mission_and_open_maintenance(
    conn: asyncpg.Connection,
    *,
    vehicle_id: str,
    reason: str,
    now: datetime,
) -> tuple[int | None, int]:
    """Cancel the vehicle's active mission (if any) and create a maintenance record.

    Must run inside an existing transaction with FOR UPDATE already held on the
    vehicle row. Locks the active mission row FOR UPDATE before mutating it.

    Returns (cancelled_mission_id_or_none, maintenance_record_id).
    """
    mission_row = await conn.fetchrow(
        """
        SELECT id FROM missions
        WHERE vehicle_id = $1 AND status = 'active'
        FOR UPDATE
        """,
        vehicle_id,
    )
    mission_id: int | None = None
    if mission_row is not None:
        mission_id = mission_row["id"]
        await conn.execute(
            """
            UPDATE missions
            SET status = 'cancelled', cancelled_at = $1, cancelled_reason = $2
            WHERE id = $3
            """,
            now,
            reason,
            mission_id,
        )
    maint_row = await conn.fetchrow(
        """
        INSERT INTO maintenance_records (vehicle_id, mission_id, opened_at, reason)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        vehicle_id,
        mission_id,
        now,
        reason,
    )
    return mission_id, maint_row["id"]
