"""Codex adapter (OpenAI Codex CLI).

Codex is OpenAI's terminal-based AI coding agent.
Source: https://github.com/openai/codex

Observability tier: partial
  - Closed-source; system prompts and context strategy are not visible
  - Provider traffic can be captured through the gateway proxy if the
    harness respects OPENAI_BASE_URL (note: current Codex versions may
    require config.toml overrides via -c openai_base_url=...)
  - Sub-agent topology is not exposed

Capabilities:
  - Non-interactive mode via `codex exec` subcommand
  - Model selection via --model flag
  - Sandbox mode control (--sandbox)
  - Config overrides via -c key=value

Limitations:
  - System prompt not visible
  - Context management not exposed
  - Sub-agent topology not exposed
  - OPENAI_BASE_URL may be ignored; use -c openai_base_url=... as fallback
  - Only supports OpenAI models
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import AuthMode

# ANSI escape sequence stripper.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_codex_usage(text: str) -> TokenUsage | None:
    """Scan text for a JSON object with token usage fields.

    Codex ``exec --json`` emits JSONL objects such as::

        {"type":"turn.completed","usage":{"input_tokens":26549,...}}

    This parser walks each line, attempts ``json.loads``, and looks for
    token fields at the top level or nested under ``usage`` /
    ``info.total_token_usage`` / ``message.usage``. Returns the first
    non-zero usage found, or ``None``.
    """
    clean = _strip_ansi(text)
    decoder = json.JSONDecoder()
    # Walk the text attempting to decode JSON objects at each position.
    # This handles both JSONL (one object per line) and embedded JSON.
    idx = 0
    length = len(clean)
    while idx < length:
        # Skip to the next opening brace.
        brace = clean.find("{", idx)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(clean, brace)
        except (json.JSONDecodeError, ValueError):
            idx = brace + 1
            continue
        idx = end
        if not isinstance(obj, dict):
            continue
        usage = _extract_usage_from_obj(obj)
        if usage is not None and usage.total_tokens > 0:
            return usage
    return None


def _extract_usage_from_obj(obj: dict[str, Any]) -> TokenUsage | None:
    """Extract a TokenUsage from a parsed Codex event object.

    Codex emits token fields at several nesting levels depending on the
    event type. This checks the common paths.
    """
    candidates: list[dict[str, Any]] = []
    # Top-level (rare but possible)
    if "input_tokens" in obj or "output_tokens" in obj:
        candidates.append(obj)
    # Nested under "usage"
    usage = obj.get("usage")
    if isinstance(usage, dict):
        candidates.append(usage)
    # Nested under info.total_token_usage
    info = obj.get("info")
    if isinstance(info, dict):
        total = info.get("total_token_usage")
        if isinstance(total, dict):
            candidates.append(total)
    # Nested under message.usage
    message = obj.get("message")
    if isinstance(message, dict):
        msg_usage = message.get("usage")
        if isinstance(msg_usage, dict):
            candidates.append(msg_usage)

    for cand in candidates:
        in_tok = cand.get("input_tokens")
        out_tok = cand.get("output_tokens")
        if in_tok is None and out_tok is None:
            continue
        in_tok = in_tok if isinstance(in_tok, int) else 0
        out_tok = out_tok if isinstance(out_tok, int) else 0
        if in_tok == 0 and out_tok == 0:
            continue
        # Codex uses cached_input_tokens for cache reads and
        # reasoning_output_tokens for reasoning tokens.
        cached = cand.get("cached_input_tokens", 0)
        reasoning = cand.get("reasoning_output_tokens", 0)
        return TokenUsage(
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cached if isinstance(cached, int) else 0,
            reasoning_tokens=reasoning if isinstance(reasoning, int) else 0,
        )
    return None


class CodexAdapter(BaseAdapter):
    """Adapter for OpenAI Codex CLI harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="codex",
            display_name="Codex (OpenAI)",
            observability_tier="partial",
            description="OpenAI's terminal-based AI coding agent",
            capabilities=[
                "Non-interactive mode via codex exec",
                "Model selection via --model",
                "Sandbox mode control",
                "Config overrides via -c",
            ],
            limitations=[
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "OPENAI_BASE_URL may be ignored; use -c openai_base_url",
                "Only supports OpenAI models",
            ],
            requires_install=True,
            install_instructions="npm install -g @openai/codex",
        )

    async def prepare(self) -> None:
        """Verify that codex is installed and on PATH."""
        self._assert_installed("codex")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Codex with the given task prompt using `codex exec`."""
        return await self._run_binary("codex", task_prompt, timeout)

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from Codex CLI output.

        Codex may emit a JSON object with ``input_tokens`` and
        ``output_tokens`` fields (e.g. in a trailing usage summary or a
        structured ``usage`` block). This parser scans both stdout and
        stderr for a JSON object containing token fields. Returns
        ``None`` when no usage is found.
        """
        for text in (stdout, stderr):
            if not text:
                continue
            usage = _extract_codex_usage(text)
            if usage is not None:
                return usage
        return None

    def _codex_gateway_url(self, url: str) -> str:
        """Replace the ``/v1`` path segment with ``/codex`` for ChatGPT auth.

        ``_gateway_url_with_trace`` appends ``/v1`` for the openai provider;
        the Codex ChatGPT auth mode routes through ``/codex`` instead.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[:-3]
        parsed = parsed._replace(path=path + "/codex")
        return urlunparse(parsed)

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the codex command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        codex_bin = "codex"

        # Use `codex exec` for non-interactive execution
        cmd = [
            codex_bin,
            "exec",
            "--model", self.model.name,
        ]

        # Add sandbox mode (default: workspace-write for evals)
        sandbox_mode = self.config.get("sandbox", "workspace-write")
        cmd.extend(["--sandbox", sandbox_mode])

        # If we have a gateway URL, pass it via config override
        # since OPENAI_BASE_URL may be ignored by current Codex.
        gateway_url = self._gateway_url_with_trace()
        if gateway_url:
            if self.model.auth_mode == AuthMode.CODEX_CHATGPT:
                codex_url = self._codex_gateway_url(gateway_url)
                cmd.extend(["-c", f"chatgpt_base_url={codex_url}"])
            else:
                cmd.extend(["-c", f"openai_base_url={gateway_url}"])

        # Add any extra config overrides
        extra_config = self.config.get("config_overrides", {})
        for key, value in extra_config.items():
            cmd.extend(["-c", f"{key}={value}"])

        # The task prompt is passed as the last argument
        cmd.append(task_prompt)

        return cmd


register_adapter("codex", CodexAdapter)
