"""Anthropic API response parser.

Parses both streaming (SSE) and non-streaming responses from the Anthropic
Messages API to extract token usage metadata.

Anthropic streaming events of interest:
  - message_start: contains message.usage with input_tokens, cache_creation_input_tokens,
    cache_read_input_tokens, output_tokens (usually 0 at this point)
  - message_delta: contains usage with output_tokens (cumulative)
  - message_stop: final event

Non-streaming responses contain a top-level "usage" object.
"""

from __future__ import annotations

import json
from typing import Any

from harnessbench.gateway.models import TokenUsage


def parse_non_streaming_usage(body: dict[str, Any]) -> TokenUsage:
    """Extract token usage from a non-streaming Anthropic response body."""
    usage = body.get("usage", {})
    return _usage_from_anthropic_dict(usage)


def parse_sse_event(event_type: str, data: dict[str, Any], accumulated: TokenUsage) -> TokenUsage:
    """Update accumulated usage from a single Anthropic SSE event.

    Args:
        event_type: The SSE event type (e.g. "message_start", "message_delta").
        data: The parsed JSON data for this event.
        accumulated: The accumulated usage so far (mutated and returned).

    Returns:
        Updated TokenUsage.
    """
    if event_type == "message_start":
        # message_start contains the initial usage with input tokens
        msg = data.get("message", {})
        usage = msg.get("usage", {})
        result = _usage_from_anthropic_dict(usage)
        # Merge: message_start sets the baseline (input tokens are here)
        accumulated.input_tokens = result.input_tokens
        accumulated.cache_read_tokens = result.cache_read_tokens
        accumulated.cache_write_tokens = result.cache_write_tokens
        # output_tokens may be 0 or small at this point
        if result.output_tokens > accumulated.output_tokens:
            accumulated.output_tokens = result.output_tokens

    elif event_type == "message_delta":
        # message_delta contains cumulative output_tokens
        usage = data.get("usage", {})
        if "output_tokens" in usage:
            accumulated.output_tokens = usage["output_tokens"]

    elif event_type == "message_stop":
        # No additional usage data, but signals completion
        pass

    return accumulated


def _usage_from_anthropic_dict(usage: dict[str, Any]) -> TokenUsage:
    """Convert Anthropic usage dict to TokenUsage."""
    return TokenUsage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
    )


def parse_sse_line(line: str) -> tuple[str | None, dict[str, Any] | None]:
    """Parse a single SSE line into (event_type, data_dict).

    SSE format:
        event: message_start
        data: {"type": "message_start", ...}

    Returns (None, None) for non-event/data lines.
    """
    if line.startswith("event: "):
        return line[len("event: ") :].strip(), None
    if line.startswith("data: "):
        raw = line[len("data: ") :].strip()
        if raw == "[DONE]":
            return None, None
        try:
            return None, json.loads(raw)
        except json.JSONDecodeError:
            return None, None
    return None, None
