"""Tests for the StreamingRedactor class."""

from __future__ import annotations

from harness_evaluator.runner.redaction import StreamingRedactor


class TestStreamingRedactorBasic:
    def test_complete_line(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"hello world\n")
        assert lines == ["hello world"]

    def test_partial_line_buffered(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"hello wor")
        assert lines == []
        lines = r.feed(b"ld\n")
        assert lines == ["hello world"]

    def test_multiple_lines_in_one_chunk(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"line1\nline2\nline3\n")
        assert lines == ["line1", "line2", "line3"]

    def test_trailing_partial_line_buffered(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"line1\nline2\npartial")
        assert lines == ["line1", "line2"]
        lines = r.feed(b"_end\n")
        assert lines == ["partial_end"]

    def test_flush_returns_remaining(self) -> None:
        r = StreamingRedactor()
        r.feed(b"line1\npartial")
        remaining = r.flush()
        assert remaining == "partial"

    def test_flush_empty_returns_none(self) -> None:
        r = StreamingRedactor()
        r.feed(b"line1\n")
        remaining = r.flush()
        assert remaining is None

    def test_empty_feed(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"")
        assert lines == []

    def test_carriage_return_not_a_split_point(self) -> None:
        """A bare \\r is NOT a line split point — the full line up to
        \\n is redacted together, so secrets split by \\r are caught."""
        r = StreamingRedactor()
        lines = r.feed(b"progress1\rprogress2\rdone\n")
        # The entire content up to \n is one line, redacted together
        assert lines == ["progress1\rprogress2\rdone"]

    def test_crlf_treated_as_single_separator(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"line1\r\nline2\r\n")
        assert lines == ["line1", "line2"]

    def test_r_does_not_split_secret(self) -> None:
        """A secret split by a bare \\r must still be redacted."""
        r = StreamingRedactor()
        lines = r.feed(b"sk-ant-abc\rdefghij\n")
        assert len(lines) == 1
        assert "sk-ant-abcdefghij" not in lines[0]
        assert "[REDACTED]" in lines[0]


class TestStreamingRedactorUTF8:
    def test_multibyte_split_across_chunks(self) -> None:
        r = StreamingRedactor()
        # "café" in UTF-8: c=0x63, a=0x61, f=0x66, é=0xc3 0xa9
        part1 = b"caf\xc3"
        part2 = b"\xa9\n"
        lines = r.feed(part1)
        assert lines == []
        lines = r.feed(part2)
        assert lines == ["café"]

    def test_replacement_on_invalid_utf8(self) -> None:
        r = StreamingRedactor()
        # Invalid UTF-8 byte
        lines = r.feed(b"\xff\xff\n")
        assert len(lines) == 1
        assert "\ufffd" in lines[0]


class TestStreamingRedactorSecrets:
    def test_redacts_sk_key_in_streamed_line(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"Error: key sk-ant-abcdefghij failed\n")
        assert len(lines) == 1
        assert "sk-ant-abcdefghij" not in lines[0]
        assert "[REDACTED]" in lines[0]

    def test_redacts_bearer_token_split_across_chunks(self) -> None:
        r = StreamingRedactor()
        part1 = b"Authorization: Bearer sk-ant-abcd"
        part2 = b"efghij\n"
        lines = r.feed(part1)
        assert lines == []
        lines = r.feed(part2)
        assert len(lines) == 1
        assert "sk-ant-abcdefghij" not in lines[0]
        assert "[REDACTED]" in lines[0]

    def test_redacts_api_key_assignment(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"ANTHROPIC_API_KEY=sk-ant-abcdefghij\n")
        assert len(lines) == 1
        assert "sk-ant-abcdefghij" not in lines[0]
        assert "[REDACTED]" in lines[0]

    def test_preserves_non_secret_text(self) -> None:
        r = StreamingRedactor()
        lines = r.feed(b"Running tests...\nAll passed.\n")
        assert lines == ["Running tests...", "All passed."]
