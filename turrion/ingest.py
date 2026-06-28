"""Collector orchestration: persist a witnessed event and wire up the graph.

resolve run/agent/entities -> trust score -> persist event (+ decision) ->
graph vertex -> causal reconstruction (trace + entity + temporal) -> divergence.
"""
from __future__ import annotations

import json
import uuid

import asyncpg

from . import graph, normalize
from .models import EventIn, IngestResult


async def _get_or_create_run(conn, external_id, ts):
    if external_id:
        row = await conn.fetchrow("SELECT id FROM runs WHERE external_id = $1", external_id)
        if row:
            return str(row["id"])
    row = await conn.fetchrow(
        "INSERT INTO runs (external_id, started_at, status) VALUES ($1, $2, 'open') RETURNING id",
        external_id, ts,
    )
    return str(row["id"])


async def ingest_event(conn, ev: EventIn) -> IngestResult:
    run_id = await _get_or_create_run(conn, ev.run_external_id, ev.ts)

    actor_id = None
    if ev.actor_kind == "agent":
        actor_id = await normalize.upsert_agent(
            conn, ev.actor_name, ev.actor_framework, ev.source_system
        )

    entity_ids = [await normalize.resolve_entity(conn, e) for e in ev.entities]
    entity_uuids = [uuid.UUID(x) for x in entity_ids]
    score = normalize.trust_score(ev)

    event_row = await conn.fetchrow(
        """
        INSERT INTO events
          (run_id, source_system, type, actor_kind, actor_id, ts,
           trace_id, span_id, parent_span_id, entity_refs, payload, trust_score)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)
        RETURNING id
        """,
        run_id, ev.source_system, ev.type, ev.actor_kind, actor_id, ev.ts,
        ev.trace_id, ev.span_id, ev.parent_span_id, entity_uuids,
        json.dumps(ev.payload), score,
    )
    event_id = str(event_row["id"])

    decision_id = None
    primary_entity = entity_ids[0] if entity_ids else None
    if ev.decision or ev.type == "decision":
        d = ev.decision
        dec_row = await conn.fetchrow(
            """
            INSERT INTO decisions
              (event_id, run_id, actor_id, entity_ref, inputs, action, field, value,
               rationale, confidence, ts)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11)
            RETURNING id
            """,
            event_id, run_id, actor_id,
            uuid.UUID(primary_entity) if primary_entity else None,
            json.dumps(d.inputs if d else {}),
            d.action if d else None,
            d.field if d else None,
            d.value if d else None,
            d.rationale if d else None,
            d.confidence if d else None,
            ev.ts,
        )
        decision_id = str(dec_row["id"])

    await graph.write_vertex(conn, "Event", event_id, {"type": ev.type})
    await graph.link_by_trace(conn, event_id, ev.parent_span_id)
    await graph.link_by_temporal_handoff(conn, event_id, ev.source_system, entity_uuids, ev.ts)

    divergence_id = None
    if decision_id:
        await graph.write_vertex(
            conn, "Decision", decision_id,
            {"action": ev.decision.action if ev.decision else None},
        )
        await graph.link_by_entity(conn, decision_id, primary_entity, ev.ts)
        d = ev.decision
        if d and d.field and d.value:
            divergence_id = await graph.detect_divergence(
                conn, primary_entity, d.field, d.value, actor_id, decision_id, ev.ts
            )

    return IngestResult(
        event_id=event_id, run_id=run_id, decision_id=decision_id,
        entity_ids=entity_ids, divergence_id=divergence_id,
    )
