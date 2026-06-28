"""Normalize / enrich: agent + entity resolution and a basic trust score.

This is the miniature 'Trust Engine' from the design doc (§4/§5): every event gets a
freshness + completeness signal so downstream causal links can be weighted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import asyncpg

from .models import EntityRef, EventIn


async def upsert_agent(
    conn: asyncpg.Connection,
    name: str | None,
    framework: str | None,
    owning_system: str | None,
) -> str | None:
    if not name:
        return None
    row = await conn.fetchrow(
        """
        INSERT INTO agents (name, owning_system, framework, version)
        VALUES ($1, $2, $3, COALESCE($4, 'v1'))
        ON CONFLICT (name, owning_system, version) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        name, owning_system, framework, None,
    )
    return str(row["id"])


async def resolve_entity(conn: asyncpg.Connection, ref: EntityRef) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO entities (type, natural_keys, system_of_record)
        VALUES ($1, $2::jsonb, $3)
        ON CONFLICT (type, natural_keys) DO UPDATE SET type = EXCLUDED.type
        RETURNING id
        """,
        ref.type, json.dumps(ref.natural_keys), ref.system_of_record,
    )
    return str(row["id"])


def trust_score(ev: EventIn) -> float:
    """Cheap, explainable score in [0,1]: completeness + recency.

    Replace with the full Ponteon Trust Engine later (provenance, source reliability).
    """
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
