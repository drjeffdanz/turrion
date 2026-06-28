"""Graph builder + causal reconstruction (design doc section 6).

Four causal bases, each edge stamped with basis + confidence:
  trace     - parent_span_id -> matching span_id      (~0.95)
  entity    - prior decision on the same entity        (~0.6)
  temporal  - cross-system handoff on the same entity  (~0.5)
  inferred  - Claude proposes a link                    (<=0.5)

Plus the conflicting-writes divergence rule. Edges live in the relational
causal_edges table; write_vertex shows the AGE 'testimony' mirror pattern.
"""
from __future__ import annotations

import json
import uuid

import asyncpg

from . import llm
from .config import settings


async def write_vertex(conn: asyncpg.Connection, label: str, key: str, props: dict) -> None:
    """Upsert a vertex into the AGE 'testimony' graph. Wrapped in a SAVEPOINT so that
    when AGE is absent the failed statement rolls back only this step, not the caller's
    surrounding transaction (otherwise ingest would hit 'transaction aborted')."""
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT * FROM cypher('testimony', $$ "
                f"MERGE (n:{label} {{key: '{key}'}}) "
                "$$) AS (v agtype);"
            )
    except Exception:
        pass


async def _add_edge(conn, from_id, to_id, relation, basis, confidence):
    if from_id == to_id:
        return
    await conn.execute(
        """
        INSERT INTO causal_edges (from_id, to_id, relation, basis, confidence)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (from_id, to_id, relation) DO NOTHING
        """,
        from_id, to_id, relation, basis, confidence,
    )


async def link_by_trace(conn, event_id, parent_span_id):
    if not parent_span_id:
        return
    parent = await conn.fetchrow(
        "SELECT id FROM events WHERE span_id = $1 ORDER BY ts DESC LIMIT 1",
        parent_span_id,
    )
    if parent:
        await _add_edge(conn, str(parent["id"]), event_id, "triggered", "trace", 0.95)


async def link_by_entity(conn, decision_id, entity_ref, ts):
    if not entity_ref:
        return
    window = settings.causal_entity_window_seconds
    prior = await conn.fetch(
        """
        SELECT id FROM decisions
        WHERE entity_ref = $1 AND id <> $2
          AND ts <  $3
          AND ts >= $3 - ($4 || ' seconds')::interval
        ORDER BY ts DESC
        LIMIT 3
        """,
        entity_ref, decision_id, ts, str(window),
    )
    for row in prior:
        await _add_edge(conn, str(row["id"]), decision_id, "used", "entity", 0.6)


async def link_by_temporal_handoff(conn, event_id, source_system, entity_uuids, ts):
    """A prior event in a DIFFERENT system touched the same entity shortly before."""
    if not entity_uuids:
        return
    window = settings.temporal_window_seconds
    rows = await conn.fetch(
        """
        SELECT id FROM events
        WHERE source_system <> $1
          AND entity_refs && $2::uuid[]
          AND ts <  $3
          AND ts >= $3 - ($4 || ' seconds')::interval
          AND id <> $5
        ORDER BY ts DESC
        LIMIT 3
        """,
        source_system, entity_uuids, ts, str(window), event_id,
    )
    for r in rows:
        await _add_edge(conn, str(r["id"]), event_id, "caused", "temporal", 0.5)


async def detect_divergence(conn, entity_ref, field, value, actor_id, decision_id, ts):
    """Conflicting-writes: different agent set same field to a different value in window."""
    if not (entity_ref and field and value):
        return None
    window = settings.divergence_window_seconds
    conflict = await conn.fetchrow(
        """
        SELECT id, value FROM decisions
        WHERE entity_ref = $1 AND field = $2
          AND value IS DISTINCT FROM $3
          AND id <> $4
          AND actor_id IS DISTINCT FROM $5
          AND ts >= $6 - ($7 || ' seconds')::interval
          AND ts <= $6 + ($7 || ' seconds')::interval
        ORDER BY ts DESC
        LIMIT 1
        """,
        entity_ref, field, value, decision_id, actor_id, ts, str(window),
    )
    if not conflict:
        return None

    pair = sorted([decision_id, str(conflict["id"])])
    pair_uuids = [uuid.UUID(p) for p in pair]
    existing = await conn.fetchrow(
        """
        SELECT id FROM divergences
        WHERE entity_ref = $1 AND rule = 'conflicting_writes'
          AND decision_ids @> $2::uuid[] AND decision_ids <@ $2::uuid[]
        """,
        entity_ref, pair_uuids,
    )
    if existing:
        return str(existing["id"])

    await _add_edge(conn, pair[0], pair[1], "contradicted", "entity", 0.8)
    row = await conn.fetchrow(
        """
        INSERT INTO divergences (entity_ref, rule, severity, decision_ids, detail)
        VALUES ($1, 'conflicting_writes', 'high', $2::uuid[], $3::jsonb)
        RETURNING id
        """,
        entity_ref, pair_uuids,
        json.dumps({"field": field, "values": [value, conflict["value"]]}),
    )
    return str(row["id"])


async def infer_links_for_run(conn, run_id):
    """Claude proposes links the mechanical bases missed (basis=inferred, conf<=0.5)."""
    decisions = await conn.fetch(
        "SELECT id, action, field, value, rationale, ts FROM decisions "
        "WHERE run_id = $1 ORDER BY ts",
        run_id,
    )
    if len(decisions) < 2 or not llm.available():
        return 0

    ids = {str(d["id"]) for d in decisions}
    listing = "\n".join(
        f"- id={d['id']} action={d['action']} field={d['field']} "
        f"value={d['value']} rationale={d['rationale']!r}"
        for d in decisions
    )
    system = (
        "You reconstruct causal links between automated decisions. Only propose a link "
        "when one plausibly caused or informed another. Use ONLY the given ids. "
        'Reply JSON: {"links":[{"from":"<id>","to":"<id>","relation":'
        '"caused|used|triggered","confidence":0.0-0.5,"why":"..."}]}.'
    )
    data = await llm.complete_json(system, f"Decisions in this run:\n{listing}")
    if not data:
        return 0

    n = 0
    for link in data.get("links", []):
        f, t = str(link.get("from", "")), str(link.get("to", ""))
        if f in ids and t in ids and f != t:
            conf = max(0.1, min(0.5, float(link.get("confidence", 0.4) or 0.4)))
            await _add_edge(conn, f, t, link.get("relation", "caused"), "inferred", conf)
            n += 1
    return n
