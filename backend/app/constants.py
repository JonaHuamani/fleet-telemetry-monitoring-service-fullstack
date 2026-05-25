ZONES: list[str] = [
    "inbound_dock_a",
    "inbound_dock_b",
    "receiving_staging",
    "aisle_a",
    "aisle_b",
    "aisle_c",
    "high_bay_1",
    "high_bay_2",
    "bulk_storage",
    "pick_zone_1",
    "pick_zone_2",
    "pack_station",
    "sort_belt",
    "outbound_dock_a",
    "outbound_dock_b",
    "shipping_staging",
    "charging_bay_1",
    "charging_bay_2",
    "charging_bay_3",
    "maintenance_bay",
]

ZONES_SET = set(ZONES)

VEHICLE_COUNT = 50
VEHICLE_IDS: list[str] = [f"v-{i}" for i in range(1, VEHICLE_COUNT + 1)]

VEHICLE_STATUSES = ("idle", "moving", "charging", "fault")

LOW_BATTERY_WARN_PCT = 15
LOW_BATTERY_CRITICAL_PCT = 5
OVERSPEED_MPS = 5.0
RAPID_BATTERY_DROP_PP = 20
RAPID_BATTERY_DROP_WINDOW_SEC = 60
STALE_TELEMETRY_SEC = 10
STALE_SCAN_INTERVAL_SEC = 5
