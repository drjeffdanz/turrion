"""Thin Anthropic (Claude) wrapper used by the inference + Ask Martus paths.

Degrades gracefully: if no ANTHROPIC_API_KEY is set, `available()` is False and callers
fall back to deterministic heuristics/templates. This keeps the whole system runnable and
testable offline, and lets Claude light it up when a key is present.
"""
from __future__ import annotations

import json
from typing import Any

from .config import settings


def available() -> bool:
    return bool(settings.anthropic_api_key)


async def complete_json(system: str, prompt: str, max_tokens: int = 1024) -> dict[str, Any] | None:
    """Ask Claude for a JSON object. Returns parsed dict, or None if unavailable/failed."""
    if not available():
        return None
    try:
        from anthropic import AsyncAnthropic
    except Exception:
        return None
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return _extract_json(text)


async def complete_text(system: str, prompt: str, max_tokens: int = 1024) -> str | None:
    if not available():
        return None
    try:
        from anthropic import AsyncAnthropic
    except Exception:
        return None
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def _extract_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None
