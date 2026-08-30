"""Base adapter class and adapter registry.

Each adapter wraps a specific coding harness (Claude Code, Codex, OpenCode, etc.)
and provides a uniform interface for the runner to:
  1. Prepare the harness environment (install, configure proxy env vars)
  2. Run the harness against a task prompt
  3. Document observability capabilities and limitations
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from harnessbench.orchestrator.config import ModelSpec


class AdapterNotInstalledError(RuntimeError):
    """Raised when a harness executable is not found on PATH."""


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

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the raw command list to run the harness, without executing it.

        This is used by the Docker runner to execute the harness CLI inside a
        container via ``docker exec``. The returned command must not depend on
        the host filesystem layout (the workdir is mounted at ``/workspace``).

        Subclasses must override this to return the harness-specific command.
        The default implementation raises ``NotImplementedError``.

        Args:
            task_prompt: The task to give to the harness.

        Returns:
            A list of command parts (argv) to execute.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_command(); "
            "it cannot be used with the Docker runner."
        )

    def _gateway_url_with_trace(self) -> str:
        """Return the gateway URL with trace_id appended as a query param.

        If no trace_id is set, returns the gateway URL unchanged.
        Preserves any existing query parameters.

        For the OpenAI provider, ``/v1`` is appended to the path so the
        base URL ends with ``/v1`` (the gateway proxy routes
        ``/v1/chat/completions`` and ``/v1/responses``).
        """
        if not self.gateway_url:
            return ""
        parsed = urlparse(self.gateway_url)
        # Append /v1 to the path for OpenAI provider if not already present.
        if self.model.provider == "openai" and not parsed.path.rstrip("/").endswith(
            "/v1"
        ):
            parsed = parsed._replace(path=parsed.path.rstrip("/") + "/v1")
        if not self.trace_id:
            return urlunparse(parsed)
        params = parse_qsl(parsed.query)
        params.append(("trace_id", self.trace_id))
        return urlunparse(parsed._replace(query=urlencode(params)))

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
            # Route provider traffic through the gateway proxy.
            # Append trace_id as a query parameter so the gateway can
            # attribute calls to this cell without relying on headers
            # (which the harness subprocesses don't reliably inject).
            gateway_url = self._gateway_url_with_trace()
            if self.model.provider == "anthropic":
                env["ANTHROPIC_BASE_URL"] = gateway_url
            elif self.model.provider == "openai":
                env["OPENAI_BASE_URL"] = gateway_url

        # Set API key from the configured env var
        api_key = os.environ.get(self.model.api_key_env, "")
        if api_key:
            if self.model.provider == "anthropic":
                env["ANTHROPIC_API_KEY"] = api_key
            elif self.model.provider == "openai":
                env["OPENAI_API_KEY"] = api_key

        # Set trace_id for per-cell cost attribution
        if self.trace_id:
            env["HARNESSBENCH_TRACE_ID"] = self.trace_id

        return env

    def _repo_dir(self) -> Path:
        """Return the directory the harness should run in.

        Prefers ``<workdir>/repo`` (created when a task has a repo_url) and
        falls back to the workdir itself for repo-less tasks.
        """
        repo = self.workdir / "repo"
        return repo if repo.exists() else self.workdir

    def _assert_installed(self, binary: str) -> None:
        """Raise AdapterNotInstalledError if ``binary`` is not on PATH."""
        if shutil.which(binary) is None:
            raise AdapterNotInstalledError(
                f"{binary} not found on PATH. {self.info().install_instructions}"
            )

    async def _run_binary(
        self, binary: str, task_prompt: str, timeout: int
    ) -> AdapterResult:
        """Shared run() implementation: verify install, build+exec the command.

        Returns an error AdapterResult (rather than raising) when the binary is
        missing on the host, so a missing harness is recorded as a failed cell
        instead of crashing the runner.
        """
        # Local import to avoid a circular import (utils imports AdapterResult).
        from harnessbench.adapters.utils import run_command

        if shutil.which(binary) is None:
            return AdapterResult(
                exit_code=-1,
                stdout="",
                stderr=f"{binary} not found. {self.info().install_instructions}",
                timed_out=False,
                duration_ms=0.0,
            )
        cmd = self.get_command(task_prompt)
        return await run_command(cmd, self._repo_dir(), self.get_env(), timeout)

    async def cleanup(self) -> None:
        """Clean up after the harness run. Override if needed."""
        return None
