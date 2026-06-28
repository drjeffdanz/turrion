# Turrion — Phases 0 + 1

The Watchtower (witness & intelligence layer) of **martus.ai**. Runnable skeleton that
ingests decision/events, reconstructs cross-system causal chains, detects agent conflicts,
and answers plain-English questions with citations.

> Full design rationale: `../turrion-mvp-design.md`.

## What's here

```
turrion/
  docker-compose.yml      # Postgres (AGE + pgvector) + Redis
  db/schema.sql           # relational tables + AGE graph + pgvector
  turrion/
    config.py  db.py  models.py
    main.py               # FastAPI: /events /runs /runs/{id}/chain /ask /divergences /runs/{id}/infer
    ingest.py             # collector pipeline
    normalize.py          # entity resolution + trust score
    graph.py              # causal reconstruction (trace/entity/temporal/inferred) + divergence rule
    llm.py                # Claude wrapper (graceful no-key fallback)
    ask.py                # Ask Martus: scope -> subgraph -> cited narrative
  sim/freight_sim.py      # the $2.1M freight scenario generator
  tests/test_smoke.py
```

## Quick start

```bash
docker compose up -d
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
psql "$DATABASE_URL" -f db/schema.sql
cp .env.example .env                 # add ANTHROPIC_API_KEY to enable Claude paths
uvicorn turrion.main:app --reload --port 8088

# seed the pitch-deck scenario
python -m sim.freight_sim --api http://localhost:8088 --decisions 847
```

Then:

```bash
curl localhost:8088/runs
curl -X POST localhost:8088/ask -H 'content-type: application/json' \
  -d '{"question":"Why did SHIP-1003 rack up so much freight cost?"}'
curl localhost:8088/divergences          # the allocator-vs-logistics conflicts
```

## What works now (Phases 0 + 1)

- Event/decision model, REST ingest, persistence, entity resolution + trust score.
- **Causal reconstruction** across all four bases: trace, entity, temporal handoff, and
  Claude-inferred (`POST /runs/{id}/infer`). Every edge carries `basis` + `confidence`.
- **Divergence detection** — conflicting-writes rule fires automatically on ingest.
- **Ask Martus** — `POST /ask`: scope extraction -> constrained subgraph -> cited answer.
  Works offline (regex + template); upgrades to Claude when `ANTHROPIC_API_KEY` is set.

## Deliberately deferred (Phase 2+)

- Timeline + causal-graph **UI** (the visual "see the chain" experience).
- pgvector semantic retrieval for Ask scope (currently identifier/keyword).
- Real connectors, OTLP endpoint, write-back/Act, multi-tenant hardening, SOC 2.

## Tests

```bash
pip install pytest && pytest -q     # offline paths: simulator, trust, scope, citations
```

## Requirements

Docker + Docker Compose · Python 3.11+ · (optional) `ANTHROPIC_API_KEY` for Claude.
