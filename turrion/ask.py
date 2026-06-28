"""Ask Martus — natural-language question -> cited causal narrative.

Pipeline (design doc §8): resolve scope -> fetch a constrained subgraph (parameterized
queries, never LLM-generated SQL) -> narrate with citations to decision ids.

Works offline: scope extraction falls back to regex heuristics and narration to a
deterministic template when no Anthropic key is set; Claude upgrades both when present.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg

from . import llm
from .models import AskResult

_SHIP_RE = re.compile(r"\b([A-Z]{2,6}-\d{2,8})\b")


async def extract_scope(question: str) -> dict[str, Any]:
    heuristic = {
        "identifier": (_SHIP_RE.search(question.upper()) or [None, None])[1]
        if _SHIP_RE.search(question.upper()) else None,
        "keywords": [w for w in ("freight", "shipment", "cost", "delay", "conflict")
                     if w in question.lower()],
        "intent": "explain",
    }
    if not llm.available():
        return heuristic
    data = await llm.complete_json(
        system=(
            "Extract query scope from a question about automated decisions. Reply JSON: "
            '{"identifier": "<entity id like SHIP-1003 or null>", '
            '"keywords": ["..."], "intent": "explain|summarize|find_conflict"}.'
        ),
        prompt=question,
    )
    if not data:
        return heuristic
    data.setdefault("keywords", heuristic["keywords"])
    data.setdefault("identifier", heuristic["identifier"])
    data.setdefault("intent", "explain")
    return data


async def _resolve_targets(conn: asyncpg.Connection, scope: dict) -> list[uuid.UUID]:
    ident = scope.get("identifier")
    if ident:
        rows = await conn.fetch(
            "SELECT id FROM entities WHERE natural_keys::text ILIKE '%' || $1 || '%'",
            ident,
        )
        if rows:
            return [r["id"] for r in rows]
    keywords = scope.get("keywords") or []
    if keywords:
        pattern = "%" + "%".join(keywords[:3]) + "%"
        rows = await conn.fetch(
            """
            SELECT DISTINCT d.entity_ref AS id FROM decisions d
            WHERE d.entity_ref IS NOT NULL
              AND (d.action ILIKE $1 OR d.rationale ILIKE $1 OR d.field ILIKE $1)
            LIMIT 50
            """,
            pattern,
        )
        if rows:
            return [r["id"] for r in rows]
    return []


async def fetch_subgraph(conn: asyncpg.Connection, scope: dict) -> dict[str, Any]:
    targets = await _resolve_targets(conn, scope)
    if targets:
        decisions = await conn.fetch(
            """
            SELECT d.id, d.action, d.field, d.value, d.rationale, d.confidence, d.ts,
                   a.name AS actor, e.natural_keys AS entity
            FROM decisions d
            LEFT JOIN agents a   ON a.id = d.actor_id
            LEFT JOIN entities e ON e.id = d.entity_ref
            WHERE d.entity_ref = ANY($1::uuid[])
            ORDER BY d.ts
            """,
            targets,
        )
        divergences = await conn.fetch(
            "SELECT id, rule, severity, decision_ids, detail, ts "
            "FROM divergences WHERE entity_ref = ANY($1::uuid[]) ORDER BY ts",
            targets,
        )
    else:
        # fall back to the most recent run
        decisions = await conn.fetch(
            """
            SELECT d.id, d.action, d.field, d.value, d.rationale, d.confidence, d.ts,
                   a.name AS actor, e.natural_keys AS entity
            FROM decisions d
            LEFT JOIN agents a   ON a.id = d.actor_id
            LEFT JOIN entities e ON e.id = d.entity_ref
            WHERE d.run_id = (SELECT id FROM runs ORDER BY started_at DESC NULLS LAST LIMIT 1)
            ORDER BY d.ts
            """,
        )
        divergences = []

    ids = [str(r["id"]) for r in decisions]
    edges = []
    if ids:
        edge_rows = await conn.fetch(
            "SELECT from_id, to_id, relation, basis, confidence FROM causal_edges "
            "WHERE from_id = ANY($1::uuid[]) AND to_id = ANY($1::uuid[])",
            ids,
        )
        edges = [dict(r) for r in edge_rows]

    def norm(rows):
        out = []
        for r in rows:
            d = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in dict(r).items()}
            if d.get("ts") is not None:
                d["ts"] = str(d["ts"])
            out.append(d)
        return out

    return {"decisions": norm(decisions), "edges": edges, "divergences": norm(divergences)}


def _template_answer(question: str, sub: dict) -> str:
    decs = sub["decisions"]
    if not decs:
        return "I don't have any witnessed decisions matching that question yet."
    lines = [f"Reconstructed {len(decs)} decision(s):"]
    for d in decs:
        ship = (d.get("entity") or {}).get("shipment_id", "?")
        lines.append(
            f"- {d['ts']}: {d.get('actor','?')} {d.get('action','?')} "
            f"({d.get('field')}={d.get('value')}) on {ship} — "
            f"{d.get('rationale','')} [{d['id']}]"
        )
    for v in sub["divergences"]:
        detail = v.get("detail") or {}
        lines.append(
            f"- CONFLICT ({v['rule']}): {detail.get('field')} set to "
            f"{detail.get('values')} by different agents [{v['id']}]"
        )
    return "\n".join(lines)


async def _llm_answer(question: str, sub: dict) -> str | None:
    system = (
        "You are martus.ai answering from a witnessed decision graph. Write a concise "
        "causal explanation. EVERY factual claim must cite a decision/divergence id in "
        "square brackets, e.g. [<id>]. If the evidence does not support an answer, say so. "
        "Do not invent ids or facts."
    )
    import json
    prompt = f"Question: {question}\n\nEvidence (JSON):\n{json.dumps(sub)[:12000]}"
    return await llm.complete_text(system, prompt, max_tokens=900)


async def ask(conn: asyncpg.Connection, question: str) -> AskResult:
    scope = await extract_scope(question)
    sub = await fetch_subgraph(conn, scope)
    citations = [d["id"] for d in sub["decisions"]] + [v["id"] for v in sub["divergences"]]

    answer = await _llm_answer(question, sub) if llm.available() else None
    used = answer is not None
    if not answer:
        answer = _template_answer(question, sub)

    return AskResult(
        question=question, answer=answer, citations=citations,
        scope=scope, subgraph=sub, llm_used=used,
    )
