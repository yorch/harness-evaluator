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

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter


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
            install_instructions="npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
        )

    async def prepare(self) -> None:
        """Verify that pi is installed and on PATH."""
        self._assert_installed("pi")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Pi with the given task prompt."""
        return await self._run_binary("pi", task_prompt, timeout)

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

        # Model selection: prefer an explicit model_flag override, otherwise
        # pass the configured ModelSpec name so the run actually uses it.
        model_flag = self.config.get("model_flag", self.model.name)
        if model_flag:
            cmd.extend(["--model", str(model_flag)])

        return cmd


register_adapter("pi", PiAdapter)
