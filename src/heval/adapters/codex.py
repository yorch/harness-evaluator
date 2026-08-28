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

import shutil
from typing import Any

from heval.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from heval.adapters.registry import register_adapter
from heval.adapters.utils import run_command


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
        """Check that codex is installed."""
        pass  # Checked at run time

    async def run(self, task_prompt: str, timeout: int = 600) -> Any:
        """Run Codex with the given task prompt using `codex exec`."""
        env = self.get_env()
        workdir = self.workdir / "repo" if (self.workdir / "repo").exists() else self.workdir

        codex_bin = shutil.which("codex")
        if not codex_bin:
            return AdapterResult(
                exit_code=-1,
                stdout="",
                stderr="codex not found. Install with: npm install -g @openai/codex",
                timed_out=False,
                duration_ms=0,
            )

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
        # since OPENAI_BASE_URL may be ignored by current Codex
        if self.gateway_url:
            cmd.extend(["-c", f"openai_base_url=\"{self.gateway_url}\""])

        # Add any extra config overrides
        extra_config = self.config.get("config_overrides", {})
        for key, value in extra_config.items():
            cmd.extend(["-c", f"{key}={value}"])

        # The task prompt is passed as the last argument
        cmd.append(task_prompt)

        return await run_command(cmd, workdir, env, timeout)


register_adapter("codex", CodexAdapter)
