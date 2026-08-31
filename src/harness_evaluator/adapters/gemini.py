"""Gemini CLI adapter.

Gemini CLI is Google's open-source terminal-based AI agent.
Source: https://github.com/google-gemini/gemini-cli

Observability tier: partial
  - Open-source (Apache-2.0); system prompts and tools are inspectable
  - Supports custom API base URL via GOOGLE_GEMINI_BASE_URL
  - Token usage available in --output-format json
  - Note: the gateway proxy does not yet support Google API routing,
    so traffic goes directly to Google's API (not through the proxy)

Capabilities:
  - Non-interactive mode via -p (print) flag
  - Model selection via --model flag
  - Output format control (text, json, stream-json)
  - MCP (Model Context Protocol) support
  - Google Search grounding

Limitations:
  - Sub-agent topology not fully exposed
  - Only supports Google Gemini models
  - Gateway proxy does not yet route Google API traffic
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


class GeminiAdapter(BaseAdapter):
    """Adapter for Gemini CLI harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="gemini",
            display_name="Gemini CLI (Google)",
            observability_tier="partial",
            description="Google's open-source terminal-based AI agent",
            capabilities=[
                "Non-interactive mode via -p (print)",
                "Model selection via --model",
                "Output format: text, json, stream-json",
                "MCP (Model Context Protocol) support",
                "Google Search grounding",
            ],
            limitations=[
                "Sub-agent topology not fully exposed",
                "Only supports Google Gemini models",
                "Gateway proxy does not yet route Google API traffic",
            ],
            requires_install=True,
            install_instructions="npm install -g @google/gemini-cli",
        )

    async def prepare(self) -> None:
        """Verify that gemini is installed and on PATH."""
        self._assert_installed("gemini")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Gemini CLI with the given task prompt."""
        return await self._run_binary("gemini", task_prompt, timeout)

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from Gemini CLI JSON output.

        Gemini CLI with ``--output-format json`` emits a JSON object with a
        ``stats`` field containing per-model token counts::

            {"stats": {"models": {"gemini-2.5-pro": {"tokens": {
                "prompt": 1234, "candidates": 567, "cached": 100,
                "thoughts": 200, "total": 2101
            }}}}}

        When multiple models are used, token counts are aggregated across
        all models. Returns ``None`` when no usage is found.
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
                usage = self._extract_usage_from_stats(parsed)
                if usage is not None and usage.total_tokens > 0:
                    return usage
        return None

    @staticmethod
    def _extract_usage_from_stats(obj: dict[str, Any]) -> TokenUsage | None:
        """Extract TokenUsage from a Gemini CLI JSON stats structure.

        Aggregates token counts across all models in the stats.
        """
        stats = obj.get("stats")
        if not isinstance(stats, dict):
            return None
        models = stats.get("models")
        if not isinstance(models, dict):
            return None
        total_prompt = 0
        total_candidates = 0
        total_cached = 0
        total_thoughts = 0
        found = False
        for model_data in models.values():
            if not isinstance(model_data, dict):
                continue
            tokens = model_data.get("tokens")
            if not isinstance(tokens, dict):
                continue
            prompt = tokens.get("prompt", 0)
            candidates = tokens.get("candidates", 0)
            if not isinstance(prompt, int) or not isinstance(candidates, int):
                continue
            if prompt > 0 or candidates > 0:
                found = True
            total_prompt += prompt
            total_candidates += candidates
            cached = tokens.get("cached", 0) or 0
            thoughts = tokens.get("thoughts", 0) or 0
            total_cached += cached if isinstance(cached, int) else 0
            total_thoughts += thoughts if isinstance(thoughts, int) else 0
        if not found:
            return None
        if total_prompt == 0 and total_candidates == 0:
            return None
        return TokenUsage(
            input_tokens=total_prompt,
            output_tokens=total_candidates,
            cache_read_tokens=total_cached,
            reasoning_tokens=total_thoughts,
        )

    def get_env(self) -> dict[str, str]:
        """Get environment variables for Gemini CLI.

        Sets GEMINI_API_KEY (and GOOGLE_API_KEY as fallback) from the
        model's api_key_env. The gateway proxy does not yet support
        Google API routing, so GOOGLE_GEMINI_BASE_URL is not set.
        """
        env = super().get_env()

        if self.model.provider == "google":
            key_env = self.model.api_key_env or "GEMINI_API_KEY"
            api_key = os.environ.get(key_env, "")
            if api_key:
                # Gemini CLI uses GEMINI_API_KEY for the Gemini API.
                # GOOGLE_API_KEY is used for Vertex AI. Set both so the
                # CLI finds the key regardless of which variable it checks.
                env["GEMINI_API_KEY"] = api_key
                env["GOOGLE_API_KEY"] = api_key

        return env

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the gemini command list for execution inside a container."""
        cmd = [
            "gemini",
            "-p",  # print/non-interactive mode
            task_prompt,
            "--model", self.model.name,
        ]

        # Output format (default: json for token usage parsing)
        output_format = self.config.get("output_format", "json")
        cmd.extend(["--output-format", output_format])

        return cmd


register_adapter("gemini", GeminiAdapter)
