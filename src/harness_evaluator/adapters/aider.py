"""Aider adapter.

Aider is an open-source AI pair programming tool for the terminal.
Source: https://github.com/Aider-AI/aider

Observability tier: full
  - Open-source (Apache-2.0); can inspect system prompts and context
  - Supports multiple providers: Anthropic, OpenAI, DeepSeek, local models
  - Reports token usage in output
  - Deep git integration

Capabilities:
  - Non-interactive mode via --message flag
  - Model selection via --model flag
  - Multi-provider support (Anthropic, OpenAI, DeepSeek, Ollama, etc.)
  - Custom API base URLs via ANTHROPIC_BASE_URL / OPENAI_BASE_URL
  - Token usage reporting in output
  - Git integration with auto-commits

Limitations:
  - Requires Python runtime
  - Sub-agent topology not exposed (single agent loop)
  - No structured JSON output mode
"""

from __future__ import annotations

import os
import re

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage

# Matches "Tokens: 1234 sent, 567 received" (with optional commas)
_TOKENS_RE = re.compile(
    r"Tokens:\s*([\d,]+)\s*sent,\s*([\d,]+)\s*received",
    re.IGNORECASE,
)

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


class AiderAdapter(BaseAdapter):
    """Adapter for Aider harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="aider",
            display_name="Aider",
            observability_tier="full",
            description="Open-source AI pair programming tool for the terminal",
            capabilities=[
                "Non-interactive mode via --message",
                "Model selection via --model",
                "Multi-provider: Anthropic, OpenAI, DeepSeek, Ollama",
                "Custom API base URL via ANTHROPIC_BASE_URL / OPENAI_BASE_URL",
                "Token usage reporting in output",
                "Deep git integration",
                "Multi-file editing",
            ],
            limitations=[
                "Requires Python runtime",
                "Sub-agent topology not exposed",
                "No structured JSON output mode",
            ],
            requires_install=True,
            install_instructions="pip install aider-chat",
        )

    async def prepare(self) -> None:
        """Verify that aider is installed and on PATH."""
        self._assert_installed("aider")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Aider with the given task prompt."""
        return await self._run_binary("aider", task_prompt, timeout)

    def get_env(self) -> dict[str, str]:
        """Get environment variables for Aider.

        Aider supports multiple providers. The base class only forwards
        ANTHROPIC_API_KEY and OPENAI_API_KEY. For other providers
        (DeepSeek, Ollama, etc.), we forward the configured api_key_env
        value so the container has access to the provider key.
        """
        env = super().get_env()

        # Forward provider-specific API keys that the base class doesn't handle.
        # Aider reads standard env var names per provider.
        if self.model.provider not in ("anthropic", "openai"):
            key_env = self.model.api_key_env
            if key_env:
                api_key = os.environ.get(key_env, "")
                if api_key:
                    env[key_env] = api_key

        return env

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from Aider's output.

        Aider prints lines like ``Tokens: 1234 sent, 567 received``
        (possibly with commas in large numbers). When multiple turns
        occur, the last cumulative line is returned. Returns ``None``
        when no usage line is found.
        """
        last_usage: TokenUsage | None = None
        for text in (stdout, stderr):
            if not text:
                continue
            clean = _strip_ansi(text)
            for match in _TOKENS_RE.finditer(clean):
                sent_str = match.group(1).replace(",", "")
                recv_str = match.group(2).replace(",", "")
                try:
                    sent = int(sent_str)
                    recv = int(recv_str)
                except ValueError:
                    continue
                if sent == 0 and recv == 0:
                    continue
                last_usage = TokenUsage(
                    input_tokens=sent,
                    output_tokens=recv,
                )
        return last_usage

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the aider command list for execution inside a container."""
        cmd = [
            "aider",
            "--message", task_prompt,
            "--model", self.model.name,
            "--yes",  # skip all confirmations (essential for non-interactive)
            "--no-auto-commits",  # prevent git commits during eval
        ]

        # Allow extra config overrides
        extra_args = self.config.get("extra_args")
        if extra_args:
            if isinstance(extra_args, list):
                cmd.extend(extra_args)
            else:
                cmd.append(str(extra_args))

        return cmd


register_adapter("aider", AiderAdapter)
