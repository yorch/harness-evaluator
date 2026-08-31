"""GitHub Copilot CLI adapter.

GitHub Copilot CLI is GitHub's terminal-based AI coding agent.
Source: https://github.com/github/copilot-cli

Observability tier: minimal
  - Closed-source; uses GitHub authentication, not API keys
  - Provider traffic goes to GitHub's backend, bypasses the gateway proxy
  - No structured token usage output
  - Only billing-level cost data may be available

Capabilities:
  - Non-interactive mode via -p (print) flag
  - Model selection via --model flag
  - GitHub integration (issues, PRs, repos)
  - MCP support
  - --allow-all-tools for headless tool access

Limitations:
  - Uses GitHub auth, not API keys — traffic bypasses gateway proxy
  - System prompt not visible
  - Context management not exposed
  - Sub-agent topology not exposed
  - No token usage reporting
  - Only available with GitHub Copilot subscription
"""

from __future__ import annotations

import os

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter


class CopilotAdapter(BaseAdapter):
    """Adapter for GitHub Copilot CLI harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="copilot",
            display_name="GitHub Copilot CLI",
            observability_tier="minimal",
            description="GitHub's terminal-based AI coding agent",
            capabilities=[
                "Non-interactive mode via -p (print)",
                "Model selection via --model",
                "GitHub integration (issues, PRs, repos)",
                "MCP support",
                "--allow-all-tools for headless tool access",
            ],
            limitations=[
                "Uses GitHub auth, not API keys — traffic bypasses proxy",
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "No token usage reporting",
                "Requires GitHub Copilot subscription",
            ],
            requires_install=True,
            install_instructions="npm install -g @github/copilot",
        )

    async def prepare(self) -> None:
        """Verify that copilot is installed and on PATH."""
        self._assert_installed("copilot")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run GitHub Copilot CLI with the given task prompt."""
        return await self._run_binary("copilot", task_prompt, timeout)

    def get_env(self) -> dict[str, str]:
        """Get environment variables for Copilot CLI.

        Copilot uses GitHub authentication. We forward GITHUB_TOKEN and
        COPILOT_TOKEN if present for headless auth. We do NOT set
        ANTHROPIC_* or OPENAI_* proxy vars.
        """
        env = super().get_env()
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("OPENAI_BASE_URL", None)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

        # Forward GitHub token for headless authentication
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if github_token:
            env["GITHUB_TOKEN"] = github_token
        copilot_token = os.environ.get("COPILOT_TOKEN", "")
        if copilot_token:
            env["COPILOT_TOKEN"] = copilot_token

        return env

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the copilot command list for execution inside a container."""
        cmd = [
            "copilot",
            "-p",  # print/non-interactive mode
            task_prompt,
        ]

        # Model selection
        model_flag = self.config.get("model_flag", "--model")
        cmd.extend([model_flag, self.model.name])

        # Silent mode (suppress interactive UI elements)
        cmd.append("-s")

        # Skip user confirmation prompts
        cmd.append("--no-ask-user")

        # Allow all tools by default for non-interactive eval runs.
        # Without this, the agent hangs on tool-approval prompts.
        if not self.config.get("disable_allow_all_tools", False):
            cmd.append("--allow-all-tools")

        return cmd


register_adapter("copilot", CopilotAdapter)
