-- Turrion schema: portable across managed Postgres (Render/Neon/Supabase) and
-- self-hosted Postgres+AGE. Uses built-in gen_random_uuid() (PG13+), and treats the
-- pgvector and Apache AGE extensions as OPTIONAL — the relational tables are the
-- system of record and all Phase-1 features work without them.
-- Applied automatically on app startup (turrion/bootstrap.py); also runnable via psql.

DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'pgvector unavailable (optional).';
END $$;

DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS age;
  PERFORM ag_catalog.create_graph('testimony');
EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'Apache AGE unavailable (optional); relational tables are source of truth.';
END $$;

CREATE TABLE IF NOT EXISTS agents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    type          TEXT,
    owning_system TEXT,
    framework     TEXT,
    version       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, owning_system, version)
);

CREATE TABLE IF NOT EXISTS entities (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type              TEXT NOT NULL,
    natural_keys      JSONB NOT NULL,
    system_of_record  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (type, natural_keys)
);

CREATE TABLE IF NOT EXISTS runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id TEXT,
    trigger     TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         UUID REFERENCES runs(id),
    source_system  TEXT NOT NULL,
    type           TEXT NOT NULL,
    actor_kind     TEXT NOT NULL DEFAULT 'system',
    actor_id       UUID REFERENCES agents(id),
    ts             TIMESTAMPTZ NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    trace_id       TEXT,
    span_id        TEXT,
    parent_span_id TEXT,
    entity_refs    UUID[] DEFAULT '{}',
    payload        JSONB NOT NULL DEFAULT '{}',
    trust_score    REAL
);
CREATE INDEX IF NOT EXISTS events_run_idx     ON events(run_id);
CREATE INDEX IF NOT EXISTS events_trace_idx   ON events(trace_id);
CREATE INDEX IF NOT EXISTS events_ts_idx      ON events(ts);
CREATE INDEX IF NOT EXISTS events_entity_gin  ON events USING gin (entity_refs);
CREATE INDEX IF NOT EXISTS events_payload_gin ON events USING gin (payload);

CREATE TABLE IF NOT EXISTS decisions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id    UUID REFERENCES events(id),
    run_id      UUID REFERENCES runs(id),
    actor_id    UUID REFERENCES agents(id),
    entity_ref  UUID REFERENCES entities(id),
    inputs      JSONB NOT NULL DEFAULT '{}',
    action      TEXT,
    field       TEXT,
    value       TEXT,
    rationale   TEXT,
    confidence  REAL,
    ts          TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_run_idx    ON decisions(run_id);
CREATE INDEX IF NOT EXISTS decisions_entity_idx ON decisions(entity_ref);

CREATE TABLE IF NOT EXISTS causal_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_id     UUID NOT NULL,
    to_id       UUID NOT NULL,
    relation    TEXT NOT NULL,
    basis       TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 0.5,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_id, to_id, relation)
);

CREATE TABLE IF NOT EXISTS divergences (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_ref   UUID REFERENCES entities(id),
    rule         TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'medium',
    decision_ids UUID[] NOT NULL DEFAULT '{}',
    detail       JSONB NOT NULL DEFAULT '{}',
    ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS testimonies (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_kind  TEXT NOT NULL,
    subject_id    UUID NOT NULL,
    summary       TEXT,
    evidence_refs UUID[] NOT NULL DEFAULT '{}',
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
