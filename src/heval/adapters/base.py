"""Base adapter class and adapter registry.

Each adapter wraps a specific coding harness (Claude Code, Codex, OpenCode, etc.)
and provides a uniform interface for the runner to:
  1. Prepare the harness environment (install, configure proxy env vars)
  2. Run the harness against a task prompt
  3. Document observability capabilities and limitations
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heval.orchestrator.config import ModelSpec


@dataclass
class AdapterResult:
    """Result from running a harness adapter."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterInfo:
    """Metadata about a harness adapter."""

    name: str
    display_name: str
    observability_tier: str
    """full: open/cooperating harness, all metadata available.
    partial: closed harness but provider traffic captured through proxy.
    minimal: only total spend or billing data available."""
    description: str
    capabilities: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    requires_install: bool = True
    install_instructions: str = ""


class BaseAdapter(ABC):
    """Base class for harness adapters."""

    def __init__(
        self,
        workdir: str | Path,
        model: ModelSpec,
        gateway_url: str | None = None,
        trace_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.workdir = Path(workdir)
        self.model = model
        self.gateway_url = gateway_url
        self.trace_id = trace_id
        self.config = config or {}

    @staticmethod
    @abstractmethod
    def info() -> AdapterInfo:
        """Return metadata about this adapter."""
        ...

    @abstractmethod
    async def prepare(self) -> None:
        """Prepare the harness environment (install deps, configure, etc.)."""
        ...

    @abstractmethod
    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Run the harness against the given task prompt.

        Args:
            task_prompt: The task to give to the harness.
            timeout: Maximum execution time in seconds.

        Returns:
            AdapterResult with stdout, stderr, exit code, etc.
        """
        ...

    def get_env(self) -> dict[str, str]:
        """Get environment variables for the harness process.

        Uses an allowlist approach to avoid leaking host secrets.
        Only passes through a minimal set of env vars needed for
        harness execution, plus the gateway proxy and API key.
        """
        # Start with a minimal allowlist of env vars
        allowlist = {
            "PATH",
            "HOME",
            "USER",
            "SHELL",
            "LANG",
            "LC_ALL",
            "TERM",
            "TMPDIR",
        }
        env: dict[str, str] = {}
        for key in allowlist:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        if self.gateway_url:
            # Route provider traffic through the gateway proxy
            if self.model.provider == "anthropic":
                env["ANTHROPIC_BASE_URL"] = self.gateway_url
            elif self.model.provider == "openai":
                env["OPENAI_BASE_URL"] = self.gateway_url

        # Set API key from the configured env var
        api_key = os.environ.get(self.model.api_key_env, "")
        if api_key:
            if self.model.provider == "anthropic":
                env["ANTHROPIC_API_KEY"] = api_key
            elif self.model.provider == "openai":
                env["OPENAI_API_KEY"] = api_key

        # Set trace_id for per-cell cost attribution
        if self.trace_id:
            env["HEVAL_TRACE_ID"] = self.trace_id

        return env

    async def cleanup(self) -> None:
        """Clean up after the harness run. Override if needed."""
        return None
