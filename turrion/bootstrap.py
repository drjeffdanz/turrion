"""Startup bootstrap: apply the schema (idempotent) and optionally seed the demo
freight scenario so a fresh deploy has live, reconstructed data immediately.
"""
from __future__ import annotations

import pathlib
from datetime import timedelta

from .ingest import ingest_event
from .models import EventIn

_SCHEMA = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def apply_schema(pool) -> None:
    sql = _SCHEMA.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)


async def seed_if_empty(pool, clusters: int = 8) -> int:
    """Seed the $2.1M freight scenario through the real ingest pipeline (so causal
    edges + divergences get reconstructed) — only if there are no runs yet."""
    from sim.freight_sim import BASE, build_cluster

    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT count(*) FROM runs")
        if existing and existing > 0:
            return 0
        t = BASE
        posted = 0
        for i in range(clusters):
            for raw in build_cluster(i, t):
                ev = EventIn(**raw)
                async with conn.transaction():
                    await ingest_event(conn, ev)
                posted += 1
            t += timedelta(seconds=2)
        return posted
