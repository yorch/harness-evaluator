"""Pi adapter.

Pi is a coding agent harness.
Observability tier: minimal
  - May not support custom API endpoints
  - Provider traffic may bypass the proxy
  - Only billing-level cost data may be available

Capabilities:
  - Terminal-based coding agent
  - File editing

Limitations:
  - May not support custom API base URL
  - System prompt not visible
  - Context management not exposed
  - Sub-agent topology not exposed
  - Traffic may bypass the gateway proxy
  - Cost accounting may rely on billing data only
"""

from __future__ import annotations

import shutil
from typing import Any

from heval.adapters.base import AdapterInfo, AdapterNotInstalledError, BaseAdapter
from heval.adapters.registry import register_adapter
from heval.adapters.utils import run_command


class PiAdapter(BaseAdapter):
    """Adapter for Pi harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="pi",
            display_name="Pi",
            observability_tier="minimal",
            description="Pi coding agent harness",
            capabilities=[
                "Terminal-based coding agent",
                "File editing",
            ],
            limitations=[
                "May not support custom API base URL",
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "Traffic may bypass the gateway proxy",
                "Cost accounting may rely on billing data only",
            ],
            requires_install=True,
            install_instructions="pip install pi  # or similar",
        )

    async def prepare(self) -> None:
        """Verify that pi is installed and on PATH."""
        if not shutil.which("pi"):
            raise AdapterNotInstalledError(
                "pi not found on PATH. Install with: pip install pi"
            )

    async def run(self, task_prompt: str, timeout: int = 600) -> Any:
        """Run Pi with the given task prompt."""
        env = self.get_env()
        workdir = self.workdir / "repo" if (self.workdir / "repo").exists() else self.workdir

        pi_bin = shutil.which("pi")
        if not pi_bin:
            from heval.adapters.base import AdapterResult

            return AdapterResult(
                exit_code=-1,
                stdout="",
                stderr="pi not found. Install with: pip install pi",
                timed_out=False,
                duration_ms=0,
            )

        cmd = self.get_command(task_prompt)

        return await run_command(cmd, workdir, env, timeout)

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the pi command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        # Use the bare name so it resolves inside the container, not on the host.
        pi_bin = "pi"

        # Pi command structure: use -p/--print for one-shot mode
        cmd = [
            pi_bin,
            "-p", task_prompt,
        ]

        # Add model if configured
        if self.config.get("model_flag"):
            cmd.extend(["--model", self.config["model_flag"]])

        return cmd


register_adapter("pi", PiAdapter)
