"""Test fixtures for the Qualitara backend.

These tests assume the docker-compose Postgres is running on host port 5433.
DATABASE_URL can be overridden via environment variable to point at a different
DB. Each test starts from a clean state: telemetry/anomalies/missions/
maintenance_records are truncated, zone_counts are zeroed, and vehicles are
reset to (idle, 100 %, last_seen_at=NULL).
"""
from __future__ import annotations

import os

# Must be set before importing app.db so the module picks up the test URL.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://qualitara:qualitara@localhost:5433/qualitara",
)

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import db as appdb
from app.main import app


@pytest_asyncio.fixture
async def db_pool():
    """Initialise the asyncpg pool, run migrations, reset state, then tear down.

    Scoped per test because pytest-asyncio's default event loop scope is also
    per test — a pool bound to a closed loop would explode on the next test.
    """
    await appdb.init_pool()
    await appdb.run_migrations()
    pool = appdb.pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                TRUNCATE telemetry, anomalies, missions, maintenance_records
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute("UPDATE zone_counts SET entry_count = 0")
            await conn.execute(
                "UPDATE vehicles SET status='idle', battery_pct=100, last_seen_at=NULL"
            )

    try:
        yield pool
    finally:
        await appdb.close_pool()


@pytest_asyncio.fixture
async def client(db_pool):
    """An httpx AsyncClient wired to the FastAPI app via ASGI in-process."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
