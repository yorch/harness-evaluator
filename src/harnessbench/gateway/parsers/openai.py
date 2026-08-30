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

from harnessbench.gateway.models import TokenUsage


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

    Handles both Chat Completions (top-level ``usage`` in the final chunk when
    include_usage is set) and the Responses API (``response.completed`` events
    carry ``usage`` nested under a ``response`` object).
    """
    usage = data.get("usage")
    if usage is None and isinstance(data.get("response"), dict):
        usage = data["response"].get("usage")
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
    """Convert an OpenAI usage dict to TokenUsage.

    Supports both the Chat Completions shape
    (``prompt_tokens``/``completion_tokens`` + ``*_tokens_details``) and the
    Responses API shape (``input_tokens``/``output_tokens`` +
    ``input_tokens_details``/``output_tokens_details``).
    """
    # Prompt / input tokens (prefer Responses key, fall back to Chat key).
    prompt_tokens = usage.get("input_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("prompt_tokens", 0)

    # Completion / output tokens.
    completion_tokens = usage.get("output_tokens")
    if completion_tokens is None:
        completion_tokens = usage.get("completion_tokens", 0)

    # Cached input tokens live under prompt_tokens_details (Chat) or
    # input_tokens_details (Responses).
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_tokens = input_details.get("cached_tokens", 0)
    non_cached_input = prompt_tokens - cached_tokens

    # Reasoning tokens (o1/o3): completion_tokens_details (Chat) or
    # output_tokens_details (Responses).
    output_details = (
        usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
    )
    reasoning = output_details.get("reasoning_tokens", 0)

    return TokenUsage(
        input_tokens=non_cached_input,
        output_tokens=completion_tokens - reasoning,
        cache_read_tokens=cached_tokens,
        reasoning_tokens=reasoning,
    )
