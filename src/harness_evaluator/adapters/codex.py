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

import json
import re
from urllib.parse import urlparse, urlunparse

from harness_evaluator.adapters.base import AdapterInfo, AdapterResult, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter
from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import AuthMode

# Matches JSON objects in Codex output that contain token usage fields.
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_codex_usage(text: str) -> TokenUsage | None:
    """Scan text for a JSON object with token usage fields.

    Codex output is not guaranteed to be pure JSON, so this searches for
    embedded JSON objects containing ``input_tokens`` or ``output_tokens``.
    """
    for match in _JSON_OBJECT_RE.finditer(text):
        try:
            obj = json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        in_tok = obj.get("input_tokens")
        out_tok = obj.get("output_tokens")
        if in_tok is None and out_tok is None:
            continue
        in_tok = in_tok if isinstance(in_tok, int) else 0
        out_tok = out_tok if isinstance(out_tok, int) else 0
        if in_tok == 0 and out_tok == 0:
            continue
        return TokenUsage(
            input_tokens=in_tok,
            output_tokens=out_tok,
            reasoning_tokens=obj.get("reasoning_tokens", 0) or 0
            if isinstance(obj.get("reasoning_tokens", 0), int)
            else 0,
        )
    return None


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
        """Verify that codex is installed and on PATH."""
        self._assert_installed("codex")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run Codex with the given task prompt using `codex exec`."""
        return await self._run_binary("codex", task_prompt, timeout)

    def parse_self_reported_usage(
        self, stdout: str, stderr: str
    ) -> TokenUsage | None:
        """Parse token usage from Codex CLI output.

        Codex may emit a JSON object with ``input_tokens`` and
        ``output_tokens`` fields (e.g. in a trailing usage summary or a
        structured ``usage`` block). This parser scans both stdout and
        stderr for a JSON object containing token fields. Returns
        ``None`` when no usage is found.
        """
        for text in (stdout, stderr):
            if not text:
                continue
            usage = _extract_codex_usage(text)
            if usage is not None:
                return usage
        return None

    def _codex_gateway_url(self, url: str) -> str:
        """Replace the ``/v1`` path segment with ``/codex`` for ChatGPT auth.

        ``_gateway_url_with_trace`` appends ``/v1`` for the openai provider;
        the Codex ChatGPT auth mode routes through ``/codex`` instead.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[:-3]
        parsed = parsed._replace(path=path + "/codex")
        return urlunparse(parsed)

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the codex command list for execution inside a container."""
        # When running inside Docker, the binary is on the container PATH.
        codex_bin = "codex"

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
        # since OPENAI_BASE_URL may be ignored by current Codex.
        gateway_url = self._gateway_url_with_trace()
        if gateway_url:
            if self.model.auth_mode == AuthMode.CODEX_CHATGPT:
                codex_url = self._codex_gateway_url(gateway_url)
                cmd.extend(["-c", f"chatgpt_base_url={codex_url}"])
            else:
                cmd.extend(["-c", f"openai_base_url={gateway_url}"])

        # Add any extra config overrides
        extra_config = self.config.get("config_overrides", {})
        for key, value in extra_config.items():
            cmd.extend(["-c", f"{key}={value}"])

        # The task prompt is passed as the last argument
        cmd.append(task_prompt)

        return cmd


register_adapter("codex", CodexAdapter)
