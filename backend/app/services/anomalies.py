from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from app.constants import (
    LOW_BATTERY_CRITICAL_PCT,
    LOW_BATTERY_WARN_PCT,
    OVERSPEED_MPS,
    RAPID_BATTERY_DROP_PP,
    RAPID_BATTERY_DROP_WINDOW_SEC,
)


@dataclass(frozen=True)
class AnomalyToEmit:
    kind: str
    severity: str
    details: dict[str, Any]


def evaluate_synchronous_rules(
    *,
    prev_status: str | None,
    prev_battery_pct: int | None,
    prev_last_seen_at: datetime | None,
    prev_error_codes_empty: bool | None,
    prev_speed_overspeed: bool | None,
    new_status: str,
    new_battery_pct: int,
    new_speed_mps: float,
    new_error_codes: list[str],
    new_ts: datetime,
) -> list[AnomalyToEmit]:
    """Evaluate the five synchronous anomaly rules.

    All rules emit on *transition* (or per-event for RAPID_BATTERY_DROP) so we
    do not flood the table at 1 Hz while a vehicle stays in a faulted state.
    """
    out: list[AnomalyToEmit] = []

    # LOW_BATTERY — emit on first crossing below the warn threshold, and again
    # if it crosses the critical threshold.
    crossed_warn = (
        new_battery_pct < LOW_BATTERY_WARN_PCT
        and (prev_battery_pct is None or prev_battery_pct >= LOW_BATTERY_WARN_PCT)
    )
    crossed_critical = (
        new_battery_pct < LOW_BATTERY_CRITICAL_PCT
        and (prev_battery_pct is None or prev_battery_pct >= LOW_BATTERY_CRITICAL_PCT)
    )
    if crossed_critical:
        out.append(
            AnomalyToEmit(
                kind="LOW_BATTERY",
                severity="critical",
                details={
                    "battery_pct": new_battery_pct,
                    "threshold": LOW_BATTERY_CRITICAL_PCT,
                },
            )
        )
    elif crossed_warn:
        out.append(
            AnomalyToEmit(
                kind="LOW_BATTERY",
                severity="warning",
                details={
                    "battery_pct": new_battery_pct,
                    "threshold": LOW_BATTERY_WARN_PCT,
                },
            )
        )

    # FAULT_STATUS — emit on transition into fault.
    if new_status == "fault" and prev_status != "fault":
        out.append(
            AnomalyToEmit(
                kind="FAULT_STATUS",
                severity="critical",
                details={"prev_status": prev_status},
            )
        )

    # ERROR_CODES_PRESENT — emit when the set transitions from empty to non-empty.
    new_codes_present = len(new_error_codes) > 0
    prev_codes_present = (prev_error_codes_empty is False) if prev_error_codes_empty is not None else False
    if new_codes_present and not prev_codes_present:
        out.append(
            AnomalyToEmit(
                kind="ERROR_CODES_PRESENT",
                severity="warning",
                details={"error_codes": new_error_codes},
            )
        )

    # OVERSPEED — emit on transition into overspeed.
    new_overspeed = new_speed_mps > OVERSPEED_MPS
    if new_overspeed and not (prev_speed_overspeed or False):
        out.append(
            AnomalyToEmit(
                kind="OVERSPEED",
                severity="warning",
                details={
                    "speed_mps": new_speed_mps,
                    "limit_mps": OVERSPEED_MPS,
                },
            )
        )

    # RAPID_BATTERY_DROP — drop of more than RAPID_BATTERY_DROP_PP pp over a
    # window shorter than RAPID_BATTERY_DROP_WINDOW_SEC.
    if (
        prev_battery_pct is not None
        and prev_last_seen_at is not None
        and (prev_battery_pct - new_battery_pct) > RAPID_BATTERY_DROP_PP
        and (new_ts - prev_last_seen_at) < timedelta(seconds=RAPID_BATTERY_DROP_WINDOW_SEC)
    ):
        out.append(
            AnomalyToEmit(
                kind="RAPID_BATTERY_DROP",
                severity="warning",
                details={
                    "prev_battery_pct": prev_battery_pct,
                    "battery_pct": new_battery_pct,
                    "delta_pp": prev_battery_pct - new_battery_pct,
                    "window_sec": (new_ts - prev_last_seen_at).total_seconds(),
                },
            )
        )

    return out


async def insert_anomalies(
    conn: asyncpg.Connection,
    *,
    vehicle_id: str,
    ts: datetime,
    anomalies: list[AnomalyToEmit],
) -> list[int]:
    if not anomalies:
        return []
    ids: list[int] = []
    for a in anomalies:
        row = await conn.fetchrow(
            """
            INSERT INTO anomalies (vehicle_id, ts, kind, severity, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            vehicle_id,
            ts,
            a.kind,
            a.severity,
            json.dumps(a.details),
        )
        ids.append(row["id"])
    return ids
