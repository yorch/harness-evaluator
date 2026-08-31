"""Antigravity CLI adapter.

Antigravity CLI is Google's terminal coding agent, the successor to
Gemini CLI. It brings the power of Gemini models to the terminal with
structured output support.

Source: https://antigravity.google/docs/cli/

Observability tier: partial
  - Has structured JSON output with token usage metadata
  - Uses Google authentication (cached credentials)
  - Provider traffic goes to Google's backend
  - Sub-agent topology partially exposed via stream-json events
  - Note: the gateway proxy does not yet support Google API routing

Capabilities:
  - Non-interactive mode via -p (print) flag
  - Model selection via --model flag
  - Output format: text, json, stream-json (NDJSON events)
  - Token usage in JSON output metadata
  - MCP support
  - Subagents and remote subagents

Limitations:
  - Uses Google auth — traffic may bypass gateway proxy
  - System prompt not fully visible
  - Only supports Google Gemini models
  - Requires prior interactive authentication
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


class AntigravityAdapter(BaseAdapter):
    """Adapter for Antigravity CLI harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="antigravity",
            display_name="Antigravity CLI (Google)",
            observability_tier="partial",
            description="Google's terminal coding agent (successor to Gemini CLI)",
            capabilities=[
                "Non-interactive mode via -p (print)",
                "Model selection via --model",
                "Output format: text, json, stream-json",
                "Token usage in JSON output metadata",
                "MCP support",
                "Subagents and remote subagents",
            ],
            limitations=[
                "Uses Google auth — traffic may bypass gateway proxy",
                "System prompt not fully visible",
                "Only supports Google Gemini models",
                "Requires prior interactive authentication",
            ],
            requires_install=True,
            install_instructions=(
                "See https://antigravity.google/docs/cli/ for installation"
            ),
        )

    async def prepare(self) -> None:
        """Verify that agy is installed and on PATH."""
        self._assert_installed("agy")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Antigravity CLI with the given task prompt."""
        return await self._run_binary("agy", task_prompt, timeout)

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from Antigravity CLI JSON output.

        Antigravity CLI with ``--output-format json`` emits a JSON object
        with token usage at various nesting levels::

            {"usage": {"input_tokens": 1234, "output_tokens": 567}}
            {"metadata": {"usage": {"prompt_tokens": 1234, ...}}}

        This parser scans for JSON objects with token fields. Returns
        ``None`` when no usage is found.
        """
        for text in (stdout, stderr):
            if not text:
                continue
            clean = _strip_ansi(text)
            decoder = json.JSONDecoder()
            idx = 0
            length = len(clean)
            while idx < length:
                brace = clean.find("{", idx)
                if brace == -1:
                    break
                try:
                    parsed, end = decoder.raw_decode(clean, brace)
                except (json.JSONDecodeError, ValueError):
                    idx = brace + 1
                    continue
                idx = end
                if not isinstance(parsed, dict):
                    continue
                usage = self._extract_usage(parsed)
                if usage is not None and usage.total_tokens > 0:
                    return usage
        return None

    @staticmethod
    def _extract_usage(obj: dict[str, Any]) -> TokenUsage | None:
        """Extract TokenUsage from a parsed JSON object.

        Checks top-level, ``usage``, and ``metadata.usage`` nesting.
        Uses explicit None checks rather than ``or`` to avoid treating
        ``0`` as falsy.
        """
        candidates: list[dict[str, Any]] = []
        if "input_tokens" in obj or "output_tokens" in obj:
            candidates.append(obj)
        usage = obj.get("usage")
        if isinstance(usage, dict):
            candidates.append(usage)
        metadata = obj.get("metadata")
        if isinstance(metadata, dict):
            meta_usage = metadata.get("usage")
            if isinstance(meta_usage, dict):
                candidates.append(meta_usage)

        for cand in candidates:
            # Use explicit None checks to distinguish 0 from missing
            in_tok = cand.get("input_tokens")
            if in_tok is None:
                in_tok = cand.get("prompt_tokens")
            out_tok = cand.get("output_tokens")
            if out_tok is None:
                out_tok = cand.get("candidates")
            if in_tok is None and out_tok is None:
                continue
            in_tok = in_tok if isinstance(in_tok, int) else 0
            out_tok = out_tok if isinstance(out_tok, int) else 0
            if in_tok == 0 and out_tok == 0:
                continue
            cached = cand.get("cached_tokens")
            if cached is None:
                cached = cand.get("cached")
            cached = cached if isinstance(cached, int) else 0
            return TokenUsage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cached,
            )
        return None

    def get_env(self) -> dict[str, str]:
        """Get environment variables for Antigravity CLI.

        Antigravity uses Google authentication. We remove any
        ANTHROPIC_*/OPENAI_* proxy vars that the base class may set
        for other providers. The gateway proxy does not yet support
        Google API routing.
        """
        env = super().get_env()
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("OPENAI_BASE_URL", None)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        return env

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the agy command list for execution inside a container."""
        cmd = [
            "agy",
            "-p",  # print/non-interactive mode
            task_prompt,
            "--model", self.model.name,
        ]

        # Output format (default: json for token usage parsing)
        output_format = self.config.get("output_format", "json")
        cmd.extend(["--output-format", output_format])

        return cmd


register_adapter("antigravity", AntigravityAdapter)
