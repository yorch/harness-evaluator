"""Claude Code adapter.

Claude Code is Anthropic's terminal-based AI coding agent.
Source: https://docs.anthropic.com/en/docs/claude-code

Observability tier: partial
  - Closed-source; system prompts and context strategy are not visible
  - Supports custom API endpoints via ANTHROPIC_BASE_URL
  - Provider traffic can be captured through the gateway proxy
  - Sub-agent topology is not exposed

Capabilities:
  - Custom API base URL via ANTHROPIC_BASE_URL
  - Non-interactive mode via --print flag
  - Model selection via --model flag
  - Max turns control via --max-turns flag
  - Output format control (text, json)

Limitations:
  - System prompt and context management are not visible
  - Sub-agent topology is not exposed
  - Exact sampling configuration is not configurable
  - Only supports Anthropic models
"""

from __future__ import annotations

import json
import re

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


class ClaudeCodeAdapter(BaseAdapter):
    """Adapter for Claude Code harness."""

    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="claude-code",
            display_name="Claude Code",
            observability_tier="partial",
            description="Anthropic's terminal-based AI coding agent",
            capabilities=[
                "Custom API base URL via ANTHROPIC_BASE_URL",
                "Non-interactive mode via --print",
                "Model selection via --model",
                "Max turns control via --max-turns",
                "JSON output format",
            ],
            limitations=[
                "System prompt not visible",
                "Context management not exposed",
                "Sub-agent topology not exposed",
                "Sampling config not configurable",
                "Only supports Anthropic models",
            ],
            requires_install=True,
            install_instructions="npm install -g @anthropic-ai/claude-code",
        )

    async def prepare(self) -> None:
        """Verify that claude is installed and on PATH."""
        self._assert_installed("claude")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Claude Code with the given task prompt."""
        result = await self._run_binary("claude", task_prompt, timeout)

        # Parse JSON output if requested
        output_format = self.config.get("output_format", "text")
        if output_format == "json" and result.stdout:
            try:
                clean = _strip_ansi(result.stdout).strip()
                parsed = json.loads(clean)
                result.metadata["parsed_output"] = parsed
                result.metadata["num_turns"] = parsed.get("num_turns")
                result.metadata["session_id"] = parsed.get("session_id")
            except json.JSONDecodeError:
                pass

        return result

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from Claude Code JSON output.

        Claude Code with ``--output-format json`` emits a JSON object with a
        ``usage`` field containing ``input_tokens``, ``output_tokens``,
        ``cache_creation_input_tokens``, and ``cache_read_input_tokens``.
        Tolerates leading/trailing text (warnings, progress lines) by
        scanning for the first valid JSON object. Returns ``None`` when
        no usage is found.
        """
        for text in (stdout, stderr):
            if not text:
                continue
            clean = _strip_ansi(text)
            decoder = json.JSONDecoder()
            idx = 0
            length = len(clean)
            while idx < length:
                brace = clean.find("{", idx)
                if brace == -1:
                    break
                try:
                    parsed, end = decoder.raw_decode(clean, brace)
                except (json.JSONDecodeError, ValueError):
                    idx = brace + 1
                    continue
                idx = end
                if not isinstance(parsed, dict):
                    continue
                usage = parsed.get("usage")
                if not isinstance(usage, dict):
                    continue
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                if not isinstance(input_tokens, int) or not isinstance(
                    output_tokens, int
                ):
                    continue
                if input_tokens == 0 and output_tokens == 0:
                    continue
                cache_read = usage.get("cache_read_input_tokens", 0) or 0
                cache_write = usage.get("cache_creation_input_tokens", 0) or 0
                return TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read
                    if isinstance(cache_read, int)
                    else 0,
                    cache_write_tokens=cache_write
                    if isinstance(cache_write, int)
                    else 0,
                )
        return None

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the claude command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        # Use the bare name so it resolves inside the container, not on the host.
        claude_bin = "claude"

        # Build command for non-interactive mode
        cmd = [
            claude_bin,
            "-p",  # print/non-interactive mode
            task_prompt,
            "--model", self.model.name,
        ]

        # Add max turns if configured (use "is not None" so max_turns: 0 is honored)
        max_turns = self.config.get("max_turns")
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])

        # Add output format
        output_format = self.config.get("output_format", "text")
        cmd.extend(["--output-format", output_format])

        # Add allowed tools if configured
        allowed_tools = self.config.get("allowed_tools")
        if allowed_tools:
            if isinstance(allowed_tools, list):
                cmd.extend(["--allowedTools", ",".join(allowed_tools)])
            else:
                cmd.extend(["--allowedTools", str(allowed_tools)])

        if self._skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        return cmd

    @property
    def _skip_permissions(self) -> bool:
        """Whether ``--dangerously-skip-permissions`` will be passed.

        Default to skipping so claude-code can autonomously edit files without
        prompting for approval. This is safe because harness-evaluator runs
        inside ephemeral Docker containers with isolated workspaces and no host
        filesystem access. Without the flag, claude-code blocks on every file
        edit and produces no changes. Set ``dangerously_skip_permissions:
        false`` in the harness config to disable (e.g. to use --allowedTools).

        Read from one place because ``get_env`` must agree with it: the two
        deciding differently is exactly the silent failure this guards against.
        """
        return bool(self.config.get("dangerously_skip_permissions", True))

    def get_env(self) -> dict[str, str]:
        """Return the adapter env, marking the container as a sandbox.

        claude-code refuses ``--dangerously-skip-permissions`` when it is
        running as root ("cannot be used with root/sudo privileges") and exits
        before making a single API call. The runner runs containers as the
        invoking host user, so on a root host -- CI, Docker-in-Docker, or a root
        shell -- that refusal fires and every cell fails as ``no_change`` with
        zero tokens, which reads as a model that did nothing rather than a
        harness that never started.

        ``IS_SANDBOX=1`` is claude-code's supported way to say the process is
        already confined, which is precisely true here: an ephemeral container
        with ``--cap-drop=ALL`` and no host filesystem access beyond the mounted
        cell workdir. It is set only when the flag it unblocks is actually used.
        """
        env = super().get_env()
        if self._skip_permissions:
            env["IS_SANDBOX"] = "1"
        return env


register_adapter("claude-code", ClaudeCodeAdapter)
