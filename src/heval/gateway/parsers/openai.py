"""OpenAI API response parser.

Parses both streaming (SSE) and non-streaming responses from the OpenAI
Chat Completions and Responses APIs to extract token usage metadata.

OpenAI streaming: usage is only returned if stream_options.include_usage is set.
When included, the final chunk contains a "usage" object.

Non-streaming responses contain a top-level "usage" object.
"""

from __future__ import annotations

import json
from typing import Any

from heval.gateway.models import TokenUsage


def parse_sse_line(line: str) -> tuple[str | None, dict[str, Any] | None]:
    """Parse a single SSE line into (event_type, data_dict).

    OpenAI SSE format:
        data: {"id": "...", "choices": [...]}

    Returns (None, None) for non-data lines or [DONE].
    """
    if line.startswith("data: "):
        raw = line[len("data: ") :].strip()
        if raw == "[DONE]":
            return None, None
        try:
            return None, json.loads(raw)
        except json.JSONDecodeError:
            return None, None
    return None, None


def parse_non_streaming_usage(body: dict[str, Any]) -> TokenUsage:
    """Extract token usage from a non-streaming OpenAI response body."""
    usage = body.get("usage", {})
    return _usage_from_openai_dict(usage)


def parse_sse_chunk(data: dict[str, Any], accumulated: TokenUsage) -> TokenUsage:
    """Update accumulated usage from an OpenAI streaming chunk.

    The final chunk (when include_usage is set) contains the full usage.
    """
    usage = data.get("usage")
    if usage is not None:
        result = _usage_from_openai_dict(usage)
        # OpenAI streaming usage is cumulative/final, so we take the max
        accumulated.input_tokens = max(accumulated.input_tokens, result.input_tokens)
        accumulated.output_tokens = max(accumulated.output_tokens, result.output_tokens)
        accumulated.reasoning_tokens = max(
            accumulated.reasoning_tokens, result.reasoning_tokens
        )
        accumulated.cache_read_tokens = max(
            accumulated.cache_read_tokens, result.cache_read_tokens
        )
        accumulated.cache_write_tokens = max(
            accumulated.cache_write_tokens, result.cache_write_tokens
        )
    return accumulated


def _usage_from_openai_dict(usage: dict[str, Any]) -> TokenUsage:
    """Convert OpenAI usage dict to TokenUsage."""
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # OpenAI breaks down prompt tokens into cached and non-cached
    prompt_details = usage.get("prompt_tokens_details", {})
    cached_tokens = prompt_details.get("cached_tokens", 0)
    non_cached_input = prompt_tokens - cached_tokens

    # Reasoning tokens (for o1/o3 models)
    completion_details = usage.get("completion_tokens_details", {})
    reasoning = completion_details.get("reasoning_tokens", 0)

    return TokenUsage(
        input_tokens=non_cached_input,
        output_tokens=completion_tokens - reasoning,
        cache_read_tokens=cached_tokens,
        reasoning_tokens=reasoning,
    )
