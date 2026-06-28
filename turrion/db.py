"""asyncpg connection pool + small helpers.

Every connection loads Apache AGE and sets the search_path so openCypher queries
against the 'testimony' graph work alongside normal SQL.
"""
from __future__ import annotations

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    # AGE must be loaded per-session; search_path lets ag_catalog functions resolve.
    try:
        await conn.execute("LOAD 'age';")
        await conn.execute('SET search_path = ag_catalog, "$user", public;')
    except Exception:
        # AGE not installed yet (e.g. plain postgres image). Relational paths still work.
        pass


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url, init=_init_conn, min_size=1, max_size=10
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised; call connect() in app lifespan.")
    return _pool
