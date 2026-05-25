from __future__ import annotations

import os
from pathlib import Path

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://qualitara:qualitara@localhost:5432/qualitara",
)

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=20,
            command_timeout=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def run_migrations() -> None:
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    files = sorted(p for p in migrations_dir.glob("*.sql"))
    p = pool()
    async with p.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
              name TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        for f in files:
            row = await conn.fetchrow(
                "SELECT name FROM _migrations WHERE name = $1", f.name
            )
            if row is None:
                async with conn.transaction():
                    await conn.execute(f.read_text())
                    await conn.execute(
                        "INSERT INTO _migrations(name) VALUES($1)", f.name
                    )
