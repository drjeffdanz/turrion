"""Pydantic models — the ingest + Ask API contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EntityRef(BaseModel):
    type: str
    natural_keys: dict[str, Any]
    system_of_record: str | None = None


class DecisionBlock(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    action: str | None = None
    field: str | None = None
    value: str | None = None
    rationale: str | None = None
    confidence: float | None = None


class EventIn(BaseModel):
    source_system: str
    type: Literal["decision", "tool_call", "llm_call", "write", "read", "event"] = "event"
    actor_kind: Literal["agent", "human", "system"] = "system"
    actor_name: str | None = None
    actor_framework: str | None = None
    ts: datetime
    run_external_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    entities: list[EntityRef] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    decision: DecisionBlock | None = None


class IngestResult(BaseModel):
    event_id: str
    run_id: str | None = None
    decision_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    divergence_id: str | None = None


class AskRequest(BaseModel):
    question: str


class AskResult(BaseModel):
    question: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    subgraph: dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False
