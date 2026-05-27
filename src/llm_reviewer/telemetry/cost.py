from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from llm_reviewer.telemetry.config import ModelPricing


@dataclass(frozen=True)
class TokenUsage:
    input: int | None = None
    output: int | None = None
    cached: int | None = None
    total: int | None = None


def parse_codex_token_usage(text: str) -> TokenUsage:
    """Parse known Codex token counters without exporting transcript text."""
    structured = _parse_structured_usage(text)
    if structured != TokenUsage():
        return structured

    lowered = text.lower()
    input_tokens = _first_int(lowered, r"(?:input|prompt)\s+tokens?\s*[:=]\s*([0-9][0-9,]*)")
    output_tokens = _first_int(lowered, r"(?:output|completion)\s+tokens?\s*[:=]\s*([0-9][0-9,]*)")
    cached_tokens = _first_int(lowered, r"cached\s+(?:input\s+)?tokens?\s*[:=]\s*([0-9][0-9,]*)")
    total_tokens = _first_int(lowered, r"total\s+tokens?\s*[:=]\s*([0-9][0-9,]*)")

    if total_tokens is None:
        total_tokens = _first_int(lowered, r"tokens\s+used\s*\n\s*([0-9][0-9,]*)")

    if total_tokens is None and (input_tokens is not None or output_tokens is not None or cached_tokens is not None):
        total_tokens = sum(value or 0 for value in (input_tokens, output_tokens, cached_tokens))

    return TokenUsage(input=input_tokens, output=output_tokens, cached=cached_tokens, total=total_tokens)


def estimate_cost_usd(usage: TokenUsage, pricing: ModelPricing) -> float:
    input_tokens = max((usage.input or 0) - (usage.cached or 0), 0)
    output_tokens = usage.output or 0
    cached_tokens = usage.cached or 0
    cost = (
        input_tokens * pricing.input_per_1m
        + output_tokens * pricing.output_per_1m
        + cached_tokens * pricing.cached_input_per_1m
    ) / 1_000_000
    return round(cost, 8)


def _parse_structured_usage(text: str) -> TokenUsage:
    marker = "LLM_REVIEWER_USAGE:"
    for line in text.splitlines():
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        try:
            data: Any = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        return TokenUsage(
            input=_int_or_none(data.get("input_tokens")),
            output=_int_or_none(data.get("output_tokens")),
            cached=_int_or_none(data.get("cached_tokens")),
            total=_int_or_none(data.get("total_tokens")),
        )
    return TokenUsage()


def _first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return _int_or_none(match.group(1))


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None
