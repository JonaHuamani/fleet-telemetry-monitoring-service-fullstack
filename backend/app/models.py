from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

VehicleStatus = Literal["idle", "moving", "charging", "fault"]
MissionStatus = Literal["active", "completed", "cancelled"]
AnomalySeverity = Literal["info", "warning", "critical"]


class TelemetryIn(BaseModel):
    vehicle_id: str
    timestamp: datetime
    lat: float
    lon: float
    battery_pct: int = Field(ge=0, le=100)
    speed_mps: float = Field(ge=0)
    status: VehicleStatus
    error_codes: list[str] = Field(default_factory=list)
    zone_entered: str | None = None
    # Optional client-supplied identifier. If provided, ingest is idempotent:
    # repeated POSTs with the same event_id resolve to the same telemetry row
    # and do not re-apply zone counters, vehicle-state updates, or anomalies.
    event_id: UUID | None = None


class TelemetryAck(BaseModel):
    id: int
    anomalies_emitted: int
    # True when the event_id matched a previously accepted telemetry row.
    idempotent: bool = False


class VehicleOut(BaseModel):
    id: str
    status: VehicleStatus
    battery_pct: int | None
    last_seen_at: datetime | None
    latest_anomaly_kind: str | None = None
    latest_anomaly_ts: datetime | None = None
    latest_anomaly_severity: AnomalySeverity | None = None


class FleetState(BaseModel):
    idle: int
    moving: int
    charging: int
    fault: int
    total: int


class ZoneCount(BaseModel):
    zone_id: str
    entry_count: int
    updated_at: datetime | None


class AnomalyOut(BaseModel):
    id: int
    vehicle_id: str
    ts: datetime
    kind: str
    severity: AnomalySeverity
    details: dict


class MissionIn(BaseModel):
    vehicle_id: str


class MissionOut(BaseModel):
    id: int
    vehicle_id: str
    status: MissionStatus
    created_at: datetime
    cancelled_at: datetime | None
    cancelled_reason: str | None


class StatusUpdateIn(BaseModel):
    status: VehicleStatus
    reason: str | None = None


class SimulatorStartIn(BaseModel):
    tick_hz: float = Field(default=1.0, gt=0, le=10)
    vehicle_count: int = Field(default=50, ge=1, le=50)


class SimulatorBurstIn(BaseModel):
    zone_id: str
    vehicle_count: int = Field(ge=1, le=50)
    jitter_ms: int = Field(default=0, ge=0, le=1000)


class SimulatorStatus(BaseModel):
    running: bool
    tick_hz: float | None
    vehicle_count: int | None
