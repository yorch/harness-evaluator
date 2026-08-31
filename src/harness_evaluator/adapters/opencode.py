"""OpenCode adapter.

OpenCode is an open-source terminal-based AI coding agent.
Source: https://github.com/sst/opencode (or similar)

Observability tier: full
  - Open-source, can inspect system prompts and tool definitions
  - Supports custom API endpoints via env vars
  - Can capture turn-level metadata

Capabilities:
  - Custom API base URL via ANTHROPIC_BASE_URL / OPENAI_BASE_URL
  - Agent mode with tool use
  - File editing and command execution

Limitations:
  - Requires Node.js
  - Sub-agent topology may vary by configuration
"""

from __future__ import annotations

import json
import re

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage

_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_opencode_usage(text: str) -> TokenUsage | None:
    """Scan text for a JSON object with token usage fields.

    OpenCode output may contain a JSON usage summary with
    ``input_tokens`` and ``output_tokens`` fields.
    """
    for match in _JSON_OBJECT_RE.finditer(text):
        try:
            obj = json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        in_tok = obj.get("input_tokens")
        out_tok = obj.get("output_tokens")
        if in_tok is None and out_tok is None:
            continue
        in_tok = in_tok if isinstance(in_tok, int) else 0
        out_tok = out_tok if isinstance(out_tok, int) else 0
        if in_tok == 0 and out_tok == 0:
            continue
        return TokenUsage(
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
    return None


class OpenCodeAdapter(BaseAdapter):
    """Adapter for OpenCode harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="opencode",
            display_name="OpenCode",
            observability_tier="full",
            description="Open-source terminal-based AI coding agent",
            capabilities=[
                "Custom API base URL",
                "Agent mode with tool use",
                "File editing and command execution",
                "System prompt inspection",
            ],
            limitations=[
                "Requires Node.js",
                "Sub-agent topology varies by config",
            ],
            requires_install=True,
            install_instructions="npm install -g opencode-ai",
        )

    async def prepare(self) -> None:
        """Verify that opencode is installed and on PATH."""
        self._assert_installed("opencode")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run OpenCode with the given task prompt."""
        return await self._run_binary("opencode", task_prompt, timeout)

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from OpenCode output.

        OpenCode may emit a JSON object with ``input_tokens`` and
        ``output_tokens`` fields. This parser scans both stdout and
        stderr. Returns ``None`` when no usage is found.
        """
        for text in (stdout, stderr):
            if not text:
                continue
            usage = _extract_opencode_usage(text)
            if usage is not None:
                return usage
        return None

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the opencode command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        # Use the bare name so it resolves inside the container, not on the host.
        opencode_bin = "opencode"

        cmd = [
            opencode_bin,
            "run",
            task_prompt,
        ]

        # Add model flag: OpenCode expects provider/model format
        model_flag = self.config.get("model_flag")
        if model_flag:
            cmd.extend(["--model", model_flag])
        else:
            # Convert to provider/model format
            provider = self.model.provider
            cmd.extend(["--model", f"{provider}/{self.model.name}"])

        return cmd


register_adapter("opencode", OpenCodeAdapter)
