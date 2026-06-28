"""Turrion collector API (FastAPI). On startup applies schema + optional seed."""
from __future__ import annotations

import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import ask as ask_mod
from . import bootstrap, db, graph
from .config import settings
from .ingest import ingest_event
from .models import AskRequest, AskResult, EventIn, IngestResult


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    try:
        await bootstrap.apply_schema(db.pool())
        if settings.seed_on_start:
            seeded = await bootstrap.seed_if_empty(db.pool())
            print(f"[turrion] seeded {seeded} decisions")
    except Exception:
        print("[turrion] bootstrap error:\n" + traceback.format_exc())
    yield
    await db.close()


app = FastAPI(title="Turrion - martus.ai", version="0.1.1", lifespan=lifespan)

_origins = ["*"] if settings.allowed_origins.strip() == "*" else [
    o.strip() for o in settings.allowed_origins.split(",") if o.strip()
]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/healthz")
async def healthz() -> dict:
    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(503, f"db unavailable: {exc}")


@app.post("/seed")
async def seed() -> dict:
    """Manually (re)seed the freight scenario if the DB is empty."""
    try:
        n = await bootstrap.seed_if_empty(db.pool())
        return {"seeded": n}
    except Exception as exc:
        print("[turrion] seed error:\n" + traceback.format_exc())
        raise HTTPException(500, detail=str(exc))


@app.post("/events", response_model=IngestResult)
async def post_event(ev: EventIn) -> IngestResult:
    try:
        async with db.pool().acquire() as conn:
            async with conn.transaction():
                return await ingest_event(conn, ev)
    except HTTPException:
        raise
    except Exception as exc:
        print("[turrion] ingest error:\n" + traceback.format_exc())
        raise HTTPException(500, detail=f"{type(exc).__name__}: {exc}")


@app.get("/runs")
async def list_runs(limit: int = 50) -> list[dict]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.external_id, r.status, r.started_at,
                   COUNT(DISTINCT e.id) AS events, COUNT(DISTINCT d.id) AS decisions
            FROM runs r
            LEFT JOIN events e    ON e.run_id = r.id
            LEFT JOIN decisions d ON d.run_id = r.id
            GROUP BY r.id
            ORDER BY r.started_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


@app.get("/runs/{run_id}/chain")
async def run_chain(run_id: str) -> dict:
    async with db.pool().acquire() as conn:
        decisions = await conn.fetch(
            """
            SELECT d.id, d.action, d.field, d.value, d.rationale, d.confidence, d.ts,
                   a.name AS actor, e.type AS entity_type
            FROM decisions d
            LEFT JOIN agents a   ON a.id = d.actor_id
            LEFT JOIN entities e ON e.id = d.entity_ref
            WHERE d.run_id = $1
            ORDER BY d.ts
            """,
            run_id,
        )
        ids = [str(r["id"]) for r in decisions]
        edges = []
        if ids:
            edge_rows = await conn.fetch(
                "SELECT from_id, to_id, relation, basis, confidence FROM causal_edges "
                "WHERE from_id = ANY($1::uuid[]) AND to_id = ANY($1::uuid[])",
                ids,
            )
            edges = [dict(r) for r in edge_rows]
        return {"run_id": run_id, "decisions": [dict(r) for r in decisions], "edges": edges}


@app.post("/ask", response_model=AskResult)
async def ask_martus(req: AskRequest) -> AskResult:
    async with db.pool().acquire() as conn:
        return await ask_mod.ask(conn, req.question)


@app.get("/divergences")
async def list_divergences(limit: int = 50) -> list[dict]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT v.id, v.rule, v.severity, v.decision_ids, v.detail, v.ts,
                   e.natural_keys AS entity
            FROM divergences v
            LEFT JOIN entities e ON e.id = v.entity_ref
            ORDER BY v.ts DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


@app.post("/runs/{run_id}/infer")
async def infer_run(run_id: str) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            added = await graph.infer_links_for_run(conn, run_id)
    return {"run_id": run_id, "inferred_edges_added": added}
