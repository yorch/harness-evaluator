"""Tests for the Anthropic SSE parser."""

from __future__ import annotations

from heval.gateway.models import TokenUsage
from heval.gateway.parsers.anthropic import (
    parse_non_streaming_usage,
    parse_sse_event,
    parse_sse_line,
)


class TestParseNonStreaming:
    def test_basic_usage(self):
        body = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            }
        }
        usage = parse_non_streaming_usage(body)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 5

    def test_missing_usage(self):
        usage = parse_non_streaming_usage({})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_no_cache_tokens(self):
        body = {"usage": {"input_tokens": 100, "output_tokens": 50}}
        usage = parse_non_streaming_usage(body)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0


class TestParseSSEEvent:
    def test_message_start(self):
        data = {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 10,
                }
            },
        }
        accumulated = TokenUsage()
        result = parse_sse_event("message_start", data, accumulated)
        assert result.input_tokens == 100
        assert result.cache_read_tokens == 20
        assert result.cache_write_tokens == 10
        assert result.output_tokens == 0

    def test_message_delta_updates_output(self):
        accumulated = TokenUsage(input_tokens=100)
        data = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 50},
        }
        result = parse_sse_event("message_delta", data, accumulated)
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_message_stop_no_change(self):
        accumulated = TokenUsage(input_tokens=100, output_tokens=50)
        result = parse_sse_event("message_stop", {"type": "message_stop"}, accumulated)
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_full_stream_sequence(self):
        """Simulate a complete Anthropic SSE stream."""
        accumulated = TokenUsage()

        # message_start
        accumulated = parse_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 25,
                    }
                },
            },
            accumulated,
        )
        assert accumulated.input_tokens == 200
        assert accumulated.cache_read_tokens == 50
        assert accumulated.cache_write_tokens == 25

        # message_delta (cumulative output)
        accumulated = parse_sse_event(
            "message_delta",
            {"type": "message_delta", "usage": {"output_tokens": 75}},
            accumulated,
        )
        assert accumulated.output_tokens == 75

        # message_stop
        accumulated = parse_sse_event(
            "message_stop",
            {"type": "message_stop"},
            accumulated,
        )
        assert accumulated.total_tokens == 350  # 200 + 75 + 50 + 25


class TestParseSSELine:
    def test_event_line(self):
        event_type, data = parse_sse_line("event: message_start")
        assert event_type == "message_start"
        assert data is None

    def test_data_line(self):
        event_type, data = parse_sse_line('data: {"type": "message_start"}')
        assert event_type is None
        assert data == {"type": "message_start"}

    def test_done_line(self):
        event_type, data = parse_sse_line("data: [DONE]")
        assert event_type is None
        assert data is None

    def test_empty_line(self):
        event_type, data = parse_sse_line("")
        assert event_type is None
        assert data is None

    def test_invalid_json(self):
        event_type, data = parse_sse_line("data: {invalid}")
        assert event_type is None
        assert data is None
