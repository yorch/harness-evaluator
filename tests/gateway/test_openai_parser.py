"""Tests for the OpenAI response parser."""

from __future__ import annotations

from heval.gateway.models import TokenUsage
from heval.gateway.parsers.openai import (
    parse_non_streaming_usage,
    parse_sse_chunk,
)


class TestParseNonStreaming:
    def test_basic_usage(self):
        body = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        }
        usage = parse_non_streaming_usage(body)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 0

    def test_with_cached_tokens(self):
        body = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 30},
            }
        }
        usage = parse_non_streaming_usage(body)
        assert usage.input_tokens == 70  # 100 - 30 cached
        assert usage.cache_read_tokens == 30
        assert usage.output_tokens == 50

    def test_with_reasoning_tokens(self):
        body = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 80,
                "total_tokens": 180,
                "completion_tokens_details": {"reasoning_tokens": 30},
            }
        }
        usage = parse_non_streaming_usage(body)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50  # 80 - 30 reasoning
        assert usage.reasoning_tokens == 30

    def test_missing_usage(self):
        usage = parse_non_streaming_usage({})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestParseSSEChunk:
    def test_chunk_without_usage(self):
        accumulated = TokenUsage(input_tokens=50)
        data = {"id": "test", "choices": [{"delta": {"content": "hi"}}]}
        result = parse_sse_chunk(data, accumulated)
        assert result.input_tokens == 50  # unchanged

    def test_chunk_with_usage(self):
        accumulated = TokenUsage()
        data = {
            "id": "test",
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        result = parse_sse_chunk(data, accumulated)
        assert result.input_tokens == 100
        assert result.output_tokens == 50
