"""Tests for secret redaction and output truncation."""

from __future__ import annotations

from harness_evaluator.runner.redaction import (
    MAX_OUTPUT_BYTES,
    redact_secrets,
    sanitize_output,
    truncate_output,
)


class TestRedactSecrets:
    def test_redacts_bearer_token(self) -> None:
        text = "Authorization: Bearer sk-ant-abc123def456ghi789"
        result = redact_secrets(text)
        assert "sk-ant-abc123def456ghi789" not in result
        assert "[REDACTED]" in result

    def test_redacts_anthropic_api_key(self) -> None:
        text = "ANTHROPIC_API_KEY=sk-ant-abc123def456ghi789"
        result = redact_secrets(text)
        assert "sk-ant-abc123def456ghi789" not in result
        assert "[REDACTED]" in result

    def test_redacts_openai_api_key(self) -> None:
        text = "OPENAI_API_KEY=sk-proj-abcdefghij1234567890"
        result = redact_secrets(text)
        assert "sk-proj-abcdefghij1234567890" not in result
        assert "[REDACTED]" in result

    def test_redacts_oauth_token(self) -> None:
        text = "CLAUDE_CODE_OAUTH_TOKEN=abc123def456ghi789jkl"
        result = redact_secrets(text)
        assert "abc123def456ghi789jkl" not in result
        assert "[REDACTED]" in result

    def test_redacts_sk_prefix_anywhere(self) -> None:
        text = "Error: invalid key sk-ant-xyz123456789"
        result = redact_secrets(text)
        assert "sk-ant-xyz123456789" not in result
        assert "[REDACTED]" in result

    def test_redacts_json_api_key(self) -> None:
        text = '{"api_key": "sk-ant-abcdefghij"}'
        result = redact_secrets(text)
        assert "sk-ant-abcdefghij" not in result
        assert "[REDACTED]" in result

    def test_preserves_non_secret_text(self) -> None:
        text = "Building project...\nRunning tests...\nAll tests passed."
        result = redact_secrets(text)
        assert result == text

    def test_redacts_multiple_secrets(self) -> None:
        text = (
            "ANTHROPIC_API_KEY=sk-ant-abc123def456\n"
            "OPENAI_API_KEY=sk-proj-xyz123abc789\n"
            "Bearer sk-ant-qq1234567890"
        )
        result = redact_secrets(text)
        assert "sk-ant-abc123def456" not in result
        assert "sk-proj-xyz123abc789" not in result
        assert "sk-ant-qq1234567890" not in result
        assert result.count("[REDACTED]") >= 3

    def test_handles_empty_string(self) -> None:
        assert redact_secrets("") == ""


class TestTruncateOutput:
    def test_short_output_unchanged(self) -> None:
        text = "short output"
        assert truncate_output(text) == text

    def test_truncates_long_output(self) -> None:
        text = "x" * (MAX_OUTPUT_BYTES + 1000)
        result = truncate_output(text)
        assert len(result.encode("utf-8")) <= MAX_OUTPUT_BYTES + 200
        assert "truncated" in result

    def test_truncation_keeps_tail(self) -> None:
        text = "HEADER\n" + "x" * (MAX_OUTPUT_BYTES + 500) + "\nTAIL_MARKER"
        result = truncate_output(text)
        assert "TAIL_MARKER" in result
        assert "truncated" in result

    def test_custom_max_bytes(self) -> None:
        text = "a" * 200
        result = truncate_output(text, max_bytes=50)
        assert "truncated" in result
        assert len(result.encode("utf-8")) <= 250


class TestSanitizeOutput:
    def test_redacts_and_truncates(self) -> None:
        # Secret at the end (tail is kept after truncation).
        text = "x" * (MAX_OUTPUT_BYTES + 500) + "\nANTHROPIC_API_KEY=sk-ant-abc123def456"
        result = sanitize_output(text)
        assert "sk-ant-abc123def456" not in result
        assert "[REDACTED]" in result
        assert "truncated" in result

    def test_handles_empty(self) -> None:
        assert sanitize_output("") == ""
