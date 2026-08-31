"""Redaction of secrets from harness stdout/stderr before persistence.

Harnesses (claude-code, codex, opencode, etc.) may print API keys, OAuth
tokens, or bearer tokens to stdout/stderr when they encounter errors.
These utilities strip known secret patterns before the output is stored
in the results database or displayed in the dashboard.
"""

from __future__ import annotations

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
