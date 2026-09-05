"""Tests for error message enrichment with harness stderr context.

Covers the logic that appends a sanitized, bounded excerpt of harness
stderr to the evaluator's error message when a cell fails. This was
originally gated on `num_api_calls == 0` (PR #66) but is now gated on
whether the harness crashed (exit_code != 0 or timed_out) or the stderr
contains actionable error content — fixing the case where the harness
made API calls that all failed (e.g. bad API key) but still exited 0.
"""

from __future__ import annotations

from harness_evaluator.runner.redaction import (
    make_error_excerpt,
    stderr_is_actionable,
)


class TestStderrIsActionable:
    """Tests for the stderr_is_actionable content filter."""

    def test_returns_true_for_api_key_error(self) -> None:
        assert stderr_is_actionable("Error: API key is invalid")

    def test_returns_true_for_unauthorized(self) -> None:
        assert stderr_is_actionable("401 Unauthorized")

    def test_returns_true_for_traceback(self) -> None:
        assert stderr_is_actionable("Traceback (most recent call last):")

    def test_returns_true_for_connection_refused(self) -> None:
        assert stderr_is_actionable("Connection refused to host")

    def test_returns_true_for_timeout(self) -> None:
        assert stderr_is_actionable("Request timed out after 30s")

    def test_returns_true_for_500_error(self) -> None:
        assert stderr_is_actionable("500 Internal Server Error")

    def test_returns_true_for_fatal(self) -> None:
        assert stderr_is_actionable("FATAL: could not start process")

    def test_returns_true_for_authentication(self) -> None:
        assert stderr_is_actionable("Authentication failed for user")

    def test_returns_false_for_benign_warnings(self) -> None:
        assert not stderr_is_actionable("Running tests...\nwarning: deprecation")

    def test_returns_false_for_progress_output(self) -> None:
        assert not stderr_is_actionable("Installing dependencies...\nDone")

    def test_returns_false_for_empty_string(self) -> None:
        assert not stderr_is_actionable("")

    def test_returns_true_for_bearer_token(self) -> None:
        assert stderr_is_actionable("Bearer token expired")

    def test_returns_true_for_not_found(self) -> None:
        assert stderr_is_actionable("Error: command not found: claude")


class TestMakeErrorExcerpt:
    """Tests for the make_error_excerpt sanitizer."""

    def test_strips_ansi_color_codes(self) -> None:
        stderr = "\x1b[31mError: something failed\x1b[0m"
        excerpt = make_error_excerpt(stderr)
        assert "\x1b" not in excerpt
        assert "Error: something failed" in excerpt

    def test_strips_ansi_cursor_movement(self) -> None:
        stderr = "\x1b[2K\x1b[1AError: boom"
        excerpt = make_error_excerpt(stderr)
        assert "\x1b" not in excerpt
        assert "Error: boom" in excerpt

    def test_strips_osc_sequences(self) -> None:
        stderr = "\x1b]0;window title\x07Error: crash"
        excerpt = make_error_excerpt(stderr)
        assert "\x1b" not in excerpt
        assert "Error: crash" in excerpt

    def test_strips_control_characters(self) -> None:
        stderr = "Error:\x00\x07 boom\x08"
        excerpt = make_error_excerpt(stderr)
        assert "\x00" not in excerpt
        assert "\x07" not in excerpt
        assert "\x08" not in excerpt
        assert "Error: boom" in excerpt

    def test_collapses_whitespace(self) -> None:
        stderr = "Error:\n\n\n   something\n   failed"
        excerpt = make_error_excerpt(stderr)
        assert "\n" not in excerpt
        assert "  " not in excerpt  # no double spaces
        assert "Error: something failed" in excerpt

    def test_redacts_api_key(self) -> None:
        stderr = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghij1234567890"
        excerpt = make_error_excerpt(stderr)
        assert "sk-ant-api03" not in excerpt
        assert "[REDACTED]" in excerpt

    def test_redacts_bearer_token(self) -> None:
        stderr = "Authorization: Bearer sk-ant-abcdefghij1234567890"
        excerpt = make_error_excerpt(stderr)
        assert "sk-ant-abcdefghij" not in excerpt
        assert "[REDACTED]" in excerpt

    def test_redacts_secret_split_by_newline(self) -> None:
        """Secrets split by \\n should be caught because whitespace is
        collapsed *before* redaction."""
        stderr = "sk-ant-abc\n1234567890xyz"
        excerpt = make_error_excerpt(stderr)
        # The collapsed text "sk-ant-abc1234567890xyz" should be redacted
        assert "sk-ant-abc123" not in excerpt

    def test_redacts_secret_split_by_carriage_return(self) -> None:
        """Secrets split by \\r should be caught because whitespace is
        collapsed *before* redaction."""
        stderr = "sk-ant-abc\r1234567890xyz"
        excerpt = make_error_excerpt(stderr)
        assert "sk-ant-abc123" not in excerpt

    def test_truncates_on_word_boundary(self) -> None:
        long_stderr = "Error: " + "word " * 100
        excerpt = make_error_excerpt(long_stderr)
        assert len(excerpt) <= 122  # 120 + ellipsis char
        assert excerpt.endswith("\u2026")
        # Should not cut in the middle of a word
        assert not excerpt[:-1].endswith("wo")

    def test_truncates_without_word_boundary(self) -> None:
        long_stderr = "Error: " + "x" * 200
        excerpt = make_error_excerpt(long_stderr)
        assert len(excerpt) <= 122
        assert excerpt.endswith("\u2026")

    def test_preserves_short_stderr(self) -> None:
        stderr = "Error: API key is invalid"
        excerpt = make_error_excerpt(stderr)
        assert excerpt == "Error: API key is invalid"

    def test_empty_stderr_returns_empty(self) -> None:
        assert make_error_excerpt("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert make_error_excerpt("   \n\n\t  ") == ""

    def test_max_chars_parameter(self) -> None:
        stderr = "Error: " + "x" * 50
        excerpt = make_error_excerpt(stderr, max_chars=20)
        assert len(excerpt) <= 22
        assert excerpt.endswith("\u2026")

    def test_rich_markup_brackets_preserved(self) -> None:
        """Brackets in stderr should survive into the excerpt — the CLI
        escapes them at display time, so they should not be stripped here."""
        stderr = "Error: [model] not found"
        excerpt = make_error_excerpt(stderr)
        assert "[model]" in excerpt
