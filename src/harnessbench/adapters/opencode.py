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

from harnessbench.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harnessbench.adapters.registry import register_adapter


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
