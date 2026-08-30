"""Shared utilities for adapter subprocess execution."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from harnessbench.adapters.base import AdapterResult


async def run_command(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    stdin_input: str | None = None,
) -> AdapterResult:
    """Run a command asynchronously with timeout.

    Args:
        cmd: Command and arguments as a list.
        cwd: Working directory.
        env: Environment variables.
        timeout: Timeout in seconds.
        stdin_input: Optional stdin input.

    Returns:
        AdapterResult with stdout, stderr, exit code, timed_out flag.
    """
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_input else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(
                    input=stdin_input.encode() if stdin_input else None
                ),
                timeout=timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000
            return AdapterResult(
                exit_code=proc.returncode or 0,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                timed_out=False,
                duration_ms=duration_ms,
            )
        except TimeoutError:
            # Kill then drain the pipes with communicate() (do NOT wait()
            # first — that reaps the process and can lose buffered output).
            proc.kill()
            duration_ms = (time.monotonic() - start) * 1000
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=5
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
            except TimeoutError:
                stdout, stderr = "", "Process killed after timeout"

            return AdapterResult(
                exit_code=-1,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                duration_ms=duration_ms,
            )

    except FileNotFoundError as e:
        duration_ms = (time.monotonic() - start) * 1000
        return AdapterResult(
            exit_code=-1,
            stdout="",
            stderr=f"Harness executable not found: {e}",
            timed_out=False,
            duration_ms=duration_ms,
        )
