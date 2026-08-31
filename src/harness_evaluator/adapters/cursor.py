"""Cursor CLI adapter.

Cursor CLI is Cursor's terminal-based AI coding agent, providing the
same agent capabilities as the Cursor IDE in a terminal interface.
The CLI binary is called ``agent`` (not ``cursor``).

Source: https://cursor.com/docs/cli

Observability tier: minimal
  - Closed-source; uses Cursor's backend, not standard API keys
  - Provider traffic goes to Cursor's backend, bypasses the gateway proxy
  - No structured token usage output
  - Only billing-level cost data may be available

Capabilities:
  - Non-interactive mode via -p (print) flag
  - Model selection via --model flag
  - Mode selection: agent (default), plan, ask
  - --force / --yolo to apply file changes in headless mode
  - --trust to skip workspace trust prompt
  - MCP support
  - Custom rules via .cursor/rules

Limitations:
  - Uses Cursor's backend — traffic bypasses proxy
  - System prompt not visible
  - Context management not exposed
  - Sub-agent topology not exposed
  - No token usage reporting
  - Requires Cursor subscription or CURSOR_API_KEY
"""

from __future__ import annotations

import os

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter


class CursorAdapter(BaseAdapter):
    """Adapter for Cursor CLI harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="cursor",
            display_name="Cursor CLI",
            observability_tier="minimal",
            description="Cursor's terminal-based AI coding agent",
            capabilities=[
                "Non-interactive mode via -p (print)",
                "Model selection via --model",
                "Mode selection: agent (default), plan, ask",
                "--force / --yolo for headless file modifications",
                "--trust to skip workspace trust prompt",
                "MCP support",
                "Custom rules via .cursor/rules",
            ],
            limitations=[
                "Uses Cursor's backend — traffic bypasses proxy",
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "No token usage reporting",
                "Requires Cursor subscription or CURSOR_API_KEY",
            ],
            requires_install=True,
            install_instructions="curl https://cursor.com/install -fsS | bash",
        )

    async def prepare(self) -> None:
        """Verify that agent (Cursor CLI) is installed and on PATH."""
        self._assert_installed("agent")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Cursor CLI with the given task prompt."""
        return await self._run_binary("agent", task_prompt, timeout)

    def get_env(self) -> dict[str, str]:
        """Get environment variables for Cursor CLI.

        Cursor uses its own backend with CURSOR_API_KEY for headless auth.
        We do NOT set ANTHROPIC_* or OPENAI_* proxy vars.
        """
        env = super().get_env()
        # Remove any proxy/API key vars that the base class may have set
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("OPENAI_BASE_URL", None)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

        # Forward Cursor API key for headless authentication
        cursor_key = os.environ.get("CURSOR_API_KEY", "")
        if cursor_key:
            env["CURSOR_API_KEY"] = cursor_key

        return env

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the agent (Cursor CLI) command list for execution inside a container."""
        cmd = [
            "agent",
            "-p",  # print/non-interactive mode
            task_prompt,
        ]

        # Model selection
        cmd.extend(["--model", self.model.name])

        # Mode selection (default: agent; only add if non-default)
        mode = self.config.get("mode", "agent")
        if mode != "agent":
            cmd.extend(["--mode", mode])

        # Force allow commands in headless mode (default: enabled)
        # Without --force, the agent only proposes changes without applying them
        force = self.config.get("force", True)
        if force:
            cmd.append("--force")

        # Trust the workspace without prompting (headless mode only)
        cmd.append("--trust")

        return cmd


register_adapter("cursor", CursorAdapter)
