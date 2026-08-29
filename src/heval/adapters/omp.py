"""OMP adapter.

OMP is a coding agent harness.
Observability tier: minimal
  - May not support custom API endpoints
  - Provider traffic may bypass the proxy
  - Only billing-level cost data may be available

Capabilities:
  - Terminal-based coding agent
  - Multi-model support

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


class OMPAdapter(BaseAdapter):
    """Adapter for OMP harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="omp",
            display_name="OMP",
            observability_tier="minimal",
            description="OMP coding agent harness",
            capabilities=[
                "Terminal-based coding agent",
                "Multi-model support",
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
            install_instructions="npm install -g @oh-my-pi/pi-coding-agent",
        )

    async def prepare(self) -> None:
        """Verify that omp is installed and on PATH."""
        if not shutil.which("omp"):
            raise AdapterNotInstalledError(
                "omp not found on PATH. Install with: pip install omp"
            )

    async def run(self, task_prompt: str, timeout: int = 600) -> Any:
        """Run OMP with the given task prompt."""
        env = self.get_env()
        workdir = self.workdir / "repo" if (self.workdir / "repo").exists() else self.workdir

        omp_bin = shutil.which("omp")
        if not omp_bin:
            from heval.adapters.base import AdapterResult

            return AdapterResult(
                exit_code=-1,
                stdout="",
                stderr="omp not found. Install with: pip install omp",
                timed_out=False,
                duration_ms=0,
            )

        cmd = self.get_command(task_prompt)

        return await run_command(cmd, workdir, env, timeout)

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the omp command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        # Use the bare name so it resolves inside the container, not on the host.
        omp_bin = "omp"

        # OMP command structure: use -p/--print for one-shot mode
        cmd = [
            omp_bin,
            "-p", task_prompt,
        ]

        # Add model if configured
        if self.config.get("model_flag"):
            cmd.extend(["--model", self.config["model_flag"]])

        return cmd


register_adapter("omp", OMPAdapter)
