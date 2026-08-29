"""Claude Code adapter.

Claude Code is Anthropic's terminal-based AI coding agent.
Source: https://docs.anthropic.com/en/docs/claude-code

Observability tier: partial
  - Closed-source; system prompts and context strategy are not visible
  - Supports custom API endpoints via ANTHROPIC_BASE_URL
  - Provider traffic can be captured through the gateway proxy
  - Sub-agent topology is not exposed

Capabilities:
  - Custom API base URL via ANTHROPIC_BASE_URL
  - Non-interactive mode via --print flag
  - Model selection via --model flag
  - Max turns control via --max-turns flag
  - Output format control (text, json)

Limitations:
  - System prompt and context management are not visible
  - Sub-agent topology is not exposed
  - Exact sampling configuration is not configurable
  - Only supports Anthropic models
"""

from __future__ import annotations

import json
import re

from heval.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from heval.adapters.registry import register_adapter

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


class ClaudeCodeAdapter(BaseAdapter):
    """Adapter for Claude Code harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="claude-code",
            display_name="Claude Code",
            observability_tier="partial",
            description="Anthropic's terminal-based AI coding agent",
            capabilities=[
                "Custom API base URL via ANTHROPIC_BASE_URL",
                "Non-interactive mode via --print",
                "Model selection via --model",
                "Max turns control via --max-turns",
                "JSON output format",
            ],
            limitations=[
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "Sampling config not configurable",
                "Only supports Anthropic models",
            ],
            requires_install=True,
            install_instructions="npm install -g @anthropic-ai/claude-code",
        )

    async def prepare(self) -> None:
        """Verify that claude is installed and on PATH."""
        self._assert_installed("claude")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Claude Code with the given task prompt."""
        result = await self._run_binary("claude", task_prompt, timeout)

        # Parse JSON output if requested
        output_format = self.config.get("output_format", "text")
        if output_format == "json" and result.stdout:
            try:
                clean = _strip_ansi(result.stdout).strip()
                parsed = json.loads(clean)
                result.metadata["parsed_output"] = parsed
                result.metadata["num_turns"] = parsed.get("num_turns")
                result.metadata["session_id"] = parsed.get("session_id")
            except json.JSONDecodeError:
                pass

        return result

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the claude command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        # Use the bare name so it resolves inside the container, not on the host.
        claude_bin = "claude"

        # Build command for non-interactive mode
        cmd = [
            claude_bin,
            "-p",  # print/non-interactive mode
            task_prompt,
            "--model", self.model.name,
        ]

        # Add max turns if configured (use "is not None" so max_turns: 0 is honored)
        max_turns = self.config.get("max_turns")
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])

        # Add output format
        output_format = self.config.get("output_format", "text")
        cmd.extend(["--output-format", output_format])

        # Add allowed tools if configured
        allowed_tools = self.config.get("allowed_tools")
        if allowed_tools:
            if isinstance(allowed_tools, list):
                cmd.extend(["--allowedTools", ",".join(allowed_tools)])
            else:
                cmd.extend(["--allowedTools", str(allowed_tools)])

        return cmd


register_adapter("claude-code", ClaudeCodeAdapter)
