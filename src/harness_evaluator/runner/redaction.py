"""Redaction of secrets from harness stdout/stderr before persistence.

Harnesses (claude-code, codex, opencode, etc.) may print API keys, OAuth
tokens, or bearer tokens to stdout/stderr when they encounter errors.
These utilities strip known secret patterns before the output is stored
in the results database or displayed in the dashboard.
"""

from __future__ import annotations

import codecs
import re

# Maximum bytes of stdout/stderr to store per stream. We keep the *tail*
# because error messages and stack traces appear at the end of output.
MAX_OUTPUT_BYTES = 50_000

# Env var names whose values are secrets (from BaseAdapter.get_env).
_SECRET_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)

# Regex patterns for common secret formats in log output.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer tokens in headers: Authorization: Bearer sk-...
    (re.compile(r"(Bearer\s+)([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE), r"\1[REDACTED]"),
    # API key assignments: ANTHROPIC_API_KEY=sk-... or "api_key": "sk-..."
    (re.compile(r"(api[_-]?key[\"'\s:=]+)([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE), r"\1[REDACTED]"),
    # OAuth token assignments: CLAUDE_CODE_OAUTH_TOKEN=... or "token": "..."
    (re.compile(r"(token[\"'\s:=]+)([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE), r"\1[REDACTED]"),
    # sk- prefixed keys (Anthropic/OpenAI format)
    (re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"), "[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    """Remove known secret patterns from *text*.

    Replaces API keys, bearer tokens, and OAuth tokens with ``[REDACTED]``.
    """
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    # Redact known env var values: VAR_NAME=value
    for var in _SECRET_ENV_VARS:
        text = re.sub(
            rf"({var}[\"'\s:=]+)(\S+)",
            r"\1[REDACTED]",
            text,
        )
    return text


def truncate_output(text: str, max_bytes: int = MAX_OUTPUT_BYTES) -> str:
    """Truncate output to the last *max_bytes* bytes.

    Keeps the tail because error messages and stack traces appear at the
    end of harness output. If truncation occurs, a notice is prepended.
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[-max_bytes:].decode("utf-8", errors="replace")
    # Find the first newline to avoid cutting in the middle of a line.
    first_nl = truncated.find("\n")
    if first_nl != -1 and first_nl < 200:
        truncated = truncated[first_nl + 1 :]
    return f"[... output truncated, showing last {max_bytes} bytes ...]\n{truncated}"


def sanitize_output(text: str) -> str:
    """Redact secrets and truncate in one pass."""
    return truncate_output(redact_secrets(text))


class StreamingRedactor:
    """Line-by-line redactor for live harness output streams.

    Buffers incomplete lines (split on ``\\n`` or ``\\r``) and applies
    ``redact_secrets`` to each complete line before emitting it. Uses an
    incremental UTF-8 decoder so multi-byte sequences split across read
    boundaries are handled correctly.

    This is designed for the TUI's per-cell output panel: it ensures
    secrets never reach the terminal even when streaming live, without
    waiting for the full buffer to be captured (the existing
    ``sanitize_output`` runs only at persistence time).
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._line_buffer = ""

    def feed(self, data: bytes) -> list[str]:
        """Feed raw bytes and return a list of redacted complete lines.

        Lines are split on ``\\n`` (Unix) or ``\\r\\n`` (Windows). A
        bare ``\\r`` (carriage return without a following ``\\n``) is
        kept in the line buffer — it is NOT a split point. This ensures
        that a secret split by a ``\\r`` (e.g. ``sk-ant-abc\\rdefghij``)
        is redacted as a whole, because the redaction regex runs on the
        full line up to the next ``\\n``.

        However, the ``\\r`` character itself is not in the regex
        character class ``[A-Za-z0-9_\\-]``, so it would still break
        the match. To handle this, ``\\r`` characters are stripped from
        the line before redaction, reassembling the secret so the regex
        can catch it. The ``\\r`` is preserved in the emitted line for
        display purposes (terminals interpret it as a cursor reset).
        """
        decoded = self._decoder.decode(data)
        self._line_buffer += decoded
        lines: list[str] = []
        # Only split on \n (and \r\n as a single separator). A bare \r
        # is NOT a split point for redaction — the full line must be
        # redacted together so secrets split by \r are caught.
        while "\n" in self._line_buffer:
            nl_pos = self._line_buffer.find("\n")
            # Check for \r\n (Windows line ending)
            if nl_pos > 0 and self._line_buffer[nl_pos - 1] == "\r":
                line = self._line_buffer[: nl_pos - 1]
                self._line_buffer = self._line_buffer[nl_pos + 1 :]
            else:
                line = self._line_buffer[:nl_pos]
                self._line_buffer = self._line_buffer[nl_pos + 1 :]
            if line:
                # Strip \r before redaction so secrets split by \r
                # are reassembled and caught by the regex. Preserve \r
                # in the emitted line for display.
                redacted = redact_secrets(line.replace("\r", ""))
                # Re-insert \r characters at their original positions
                # if the redacted line is different (i.e. a secret was
                # found and replaced). If no redaction occurred, emit
                # the original line with \r intact.
                if redacted != line.replace("\r", ""):
                    # Redaction changed the line — emit the redacted
                    # version without \r (the secret is gone anyway).
                    lines.append(redacted)
                else:
                    lines.append(line)
        return lines

    def flush(self) -> str | None:
        """Return any remaining buffered text, redacted, or None if empty."""
        remaining = self._line_buffer
        self._line_buffer = ""
        # Also flush the decoder's internal buffer
        final = self._decoder.decode(b"", final=True)
        remaining += final
        if remaining:
            # Strip \r before redaction to match feed() behavior —
            # a secret split by a bare \r without a trailing \n must
            # still be reassembled and caught by the regex.
            return redact_secrets(remaining.replace("\r", ""))
        return None
