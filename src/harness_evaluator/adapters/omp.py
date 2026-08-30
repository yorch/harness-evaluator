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

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter


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
        self._assert_installed("omp")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run OMP with the given task prompt."""
        return await self._run_binary("omp", task_prompt, timeout)

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

        # Model selection: prefer an explicit model_flag override, otherwise
        # pass the configured ModelSpec name so the run actually uses it.
        model_flag = self.config.get("model_flag", self.model.name)
        if model_flag:
            cmd.extend(["--model", str(model_flag)])

        return cmd


register_adapter("omp", OMPAdapter)
