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


# --- Error excerpt helpers -----------------------------------------------------
# Used by the runner to enrich failed-cell error messages with a short,
# sanitized excerpt of harness stderr. The evaluator only sees the diff/test
# results, not the harness stderr, so it cannot report why the harness failed
# (e.g. API auth error, command not found, crash). These helpers produce a
# safe, bounded excerpt that can be appended to the evaluator's message.

# Broader ANSI escape stripper: covers CSI (colors, cursor, erase), OSC
# (window titles), and non-CSI escapes. The previous regex only matched
# SGR color codes ending in 'm', missing cursor movement, erase, and OSC.
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Other control characters (excluding \t \n \r which are handled by the
# whitespace collapse): null, bell, backspace, vertical tab, form feed, etc.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Maximum length of the stderr excerpt appended to error_message. Kept
# short so it fits in the CLI's "First N errors" summary without wrapping.
_ERROR_EXCERPT_MAX_CHARS = 120

# Pattern that indicates actionable error content in harness stderr. Used
# to suppress benign warnings/progress output when the harness exited 0
# but the eval still failed (e.g. no_change, wrong_approach).
_ACTIONABLE_SIGNAL_RE = re.compile(
    r"\b("
    r"error|err|exception|traceback|fatal|fail(?:ed|ure)?|"
    r"invalid|unauthorized|unauthorised|forbidden|"
    r"unreachable|connection refused|connection reset|"
    r"timeout|timed out|not found|no such file|"
    r"api key|bearer|auth(?:entication)?"
    r"|40[13]|429|50[0-5]"
    r")\b",
    re.IGNORECASE,
)


def stderr_is_actionable(stderr: str) -> bool:
    """Return True if *stderr* contains actionable error-like content.

    Used to decide whether to append a stderr excerpt to the evaluator's
    error message when the harness exited 0 (so the stderr is not the
    primary signal). When the harness crashed (exit_code != 0 or timed
    out), stderr is always considered actionable.
    """
    return bool(_ACTIONABLE_SIGNAL_RE.search(stderr))


def make_error_excerpt(stderr: str, max_chars: int = _ERROR_EXCERPT_MAX_CHARS) -> str:
    """Produce a safe, bounded excerpt of *stderr* for error messages.

    The excerpt is:
    1. Stripped of ANSI escape sequences and control characters.
    2. Whitespace-collapsed (newlines/tabs → single spaces) *before*
       redaction, so secrets split by \\r or \\n are reassembled and
       caught by the redaction regex.
    3. Redacted of known secret patterns.
    4. Truncated on a word boundary with an ellipsis.
    """
    # 1. Strip ANSI escapes and control characters
    text = _ANSI_ESCAPE_RE.sub("", stderr)
    text = _CONTROL_CHAR_RE.sub("", text)
    # 2. Collapse whitespace before redaction so split secrets are caught
    text = re.sub(r"\s+", " ", text).strip()
    # 3. Redact secrets
    text = redact_secrets(text)
    # 4. Truncate on a word boundary with ellipsis
    if len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        text = (text[:cut].rstrip() if cut > 0 else text[:max_chars]) + "\u2026"
    return text


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
        """Return any remaining buffered text, redacted, or None if empty.

        Mirrors ``feed()``: strips ``\\r`` only for the redaction match
        so a secret split by a bare ``\\r`` is reassembled and caught,
        but preserves ``\\r`` in the emitted text when no redaction
        occurred (to keep terminal carriage-return semantics).
        """
        remaining = self._line_buffer
        self._line_buffer = ""
        # Also flush the decoder's internal buffer
        final = self._decoder.decode(b"", final=True)
        remaining += final
        if remaining:
            redacted = redact_secrets(remaining.replace("\r", ""))
            if redacted != remaining.replace("\r", ""):
                # Redaction changed the text — emit the redacted version
                return redacted
            # No redaction occurred — preserve original with \r intact
            return remaining
        return None
