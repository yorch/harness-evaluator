"""Kiro CLI adapter.

Kiro CLI is AWS's terminal AI coding agent, formerly known as Amazon Q
Developer CLI. It provides agentic coding capabilities in the terminal
with AWS-backed authentication.

Source: https://kiro.dev/cli

Observability tier: minimal
  - Closed-source; uses AWS authentication (Builder ID, IAM Identity Center)
  - Provider traffic goes to AWS backend, bypasses the gateway proxy
  - No structured token usage output
  - Only billing-level cost data may be available

Capabilities:
  - Non-interactive mode via `kiro-cli chat --no-interactive`
  - Model selection via --model flag
  - Agent hooks for extensibility
  - MCP support
  - Trust level control for tools
  - Reasoning effort control

Limitations:
  - Uses AWS auth — traffic bypasses gateway proxy
  - System prompt not visible
  - Context management not exposed
  - Sub-agent topology not exposed
  - No token usage reporting
"""

from __future__ import annotations

import os

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter


class KiroAdapter(BaseAdapter):
    """Adapter for Kiro CLI harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="kiro",
            display_name="Kiro CLI (AWS)",
            observability_tier="minimal",
            description="AWS's terminal AI coding agent (formerly Amazon Q Developer CLI)",
            capabilities=[
                "Non-interactive mode via chat --no-interactive",
                "Model selection via --model",
                "Agent hooks for extensibility",
                "MCP support",
                "Trust level control for tools",
                "Reasoning effort control",
            ],
            limitations=[
                "Uses AWS auth — traffic bypasses gateway proxy",
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "No token usage reporting",
            ],
            requires_install=True,
            install_instructions="curl -fsSL https://cli.kiro.dev/install | bash",
        )

    async def prepare(self) -> None:
        """Verify that kiro-cli is installed and on PATH."""
        self._assert_installed("kiro-cli")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Kiro CLI with the given task prompt."""
        return await self._run_binary("kiro-cli", task_prompt, timeout)

    def get_env(self) -> dict[str, str]:
        """Get environment variables for Kiro CLI.

        Kiro uses AWS authentication. For headless mode, KIRO_API_KEY
        can be used. We do NOT set ANTHROPIC_* or OPENAI_* proxy vars.
        """
        env = super().get_env()
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("OPENAI_BASE_URL", None)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

        # Forward Kiro API key for headless authentication
        kiro_key = os.environ.get("KIRO_API_KEY", "")
        if kiro_key:
            env["KIRO_API_KEY"] = kiro_key

        return env

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the kiro-cli command list for execution inside a container."""
        cmd = [
            "kiro-cli",
            "chat",
            "--no-interactive",
        ]

        # Model selection
        cmd.extend(["--model", self.model.name])

        # Trust all tools by default for non-interactive eval runs.
        # Use --trust-tools=... (single token with =) per Kiro CLI docs.
        trust_tools = self.config.get("trust_tools")
        if trust_tools is not None:
            if isinstance(trust_tools, list):
                cmd.append(f"--trust-tools={','.join(trust_tools)}")
            else:
                cmd.append(f"--trust-tools={trust_tools}")
        else:
            cmd.append("--trust-all-tools")

        # Reasoning effort level
        effort = self.config.get("effort")
        if effort:
            cmd.extend(["--effort", str(effort)])

        # Agent profile selection
        agent = self.config.get("agent")
        if agent:
            cmd.extend(["--agent", str(agent)])

        # The task prompt is the last argument
        cmd.append(task_prompt)

        return cmd


register_adapter("kiro", KiroAdapter)
