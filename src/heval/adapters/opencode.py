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

import shutil
from typing import Any

from heval.adapters.base import AdapterInfo, BaseAdapter
from heval.adapters.registry import register_adapter
from heval.adapters.utils import run_command


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
            install_instructions="npm install -g @anthropic/opencode  # or similar",
        )

    async def prepare(self) -> None:
        """Check that opencode is installed."""
        # OpenCode may not be installed; we check at run time
        pass

    async def run(self, task_prompt: str, timeout: int = 600) -> Any:
        """Run OpenCode with the given task prompt."""
        env = self.get_env()
        workdir = self.workdir / "repo" if (self.workdir / "repo").exists() else self.workdir

        # Find opencode executable
        opencode_bin = shutil.which("opencode")
        if not opencode_bin:
            from heval.adapters.base import AdapterResult

            return AdapterResult(
                exit_code=-1,
                stdout="",
                stderr="opencode not found. Install with: npm install -g opencode",
                timed_out=False,
                duration_ms=0,
            )

        # Run opencode in non-interactive mode
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

        return await run_command(cmd, workdir, env, timeout)


register_adapter("opencode", OpenCodeAdapter)
