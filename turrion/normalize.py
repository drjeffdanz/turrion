"""Normalize / enrich: agent + entity resolution and a basic trust score.

Uses SELECT-then-INSERT (instead of ON CONFLICT) so it is robust across managed
Postgres where ON CONFLICT inference on a jsonb unique index can be brittle.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import asyncpg

from .models import EntityRef, EventIn


async def upsert_agent(conn, name, framework, owning_system):
    if not name:
        return None
    row = await conn.fetchrow(
        "SELECT id FROM agents WHERE name = $1 "
        "AND owning_system IS NOT DISTINCT FROM $2 AND version = 'v1'",
        name, owning_system,
    )
    if row:
        return str(row["id"])
    row = await conn.fetchrow(
        "INSERT INTO agents (name, owning_system, framework, version) "
        "VALUES ($1, $2, $3, 'v1') RETURNING id",
        name, owning_system, framework,
    )
    return str(row["id"])


async def resolve_entity(conn, ref: EntityRef) -> str:
    nk = json.dumps(ref.natural_keys, sort_keys=True)
    row = await conn.fetchrow(
        "SELECT id FROM entities WHERE type = $1 AND natural_keys = $2::jsonb",
        ref.type, nk,
    )
    if row:
        return str(row["id"])
    row = await conn.fetchrow(
        "INSERT INTO entities (type, natural_keys, system_of_record) "
        "VALUES ($1, $2::jsonb, $3) RETURNING id",
        ref.type, nk, ref.system_of_record,
    )
    return str(row["id"])


def trust_score(ev: EventIn) -> float:
    completeness = 0.0
    completeness += 0.25 if ev.actor_name else 0.0
    completeness += 0.25 if ev.entities else 0.0
    completeness += 0.25 if (ev.trace_id or ev.run_external_id) else 0.0
    completeness += 0.25 if (ev.payload or ev.decision) else 0.0

    age_s = max(0.0, (datetime.now(timezone.utc) - _aware(ev.ts)).total_seconds())
    recency = 1.0 if age_s < 3600 else max(0.3, 1.0 - age_s / (7 * 86400))
    return round(0.6 * completeness + 0.4 * recency, 3)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
