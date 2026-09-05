"""Auto-start the gateway proxy as a subprocess from the ``run`` command.

When ``harness-evaluator run`` is invoked, it needs the gateway to be running
so Docker containers can route API traffic through it for token/cost accounting.
Previously this required the user to run ``harness-evaluator gateway`` in a
separate terminal. This module provides ``GatewaySubprocess`` which spawns the
gateway as a child process, waits for it to become reachable, and shuts it down
when the ``run`` command exits — giving users a single-command experience while
preserving the ability to manage the gateway manually (if a gateway is already
reachable, no subprocess is started).

Design notes:
- The gateway is spawned via ``subprocess.Popen`` with ``sys.executable -m``
  so it uses the same Python interpreter and installed package as the parent.
- stdout/stderr are always captured to a log file (defaulting to
  ``harness_evaluator_gateway.log`` in CWD) so gateway diagnostics are not
  lost and the TUI terminal output is not corrupted.
- Cleanup uses ``atexit`` + signal handlers for SIGINT/SIGTERM so the child is
  killed even on Ctrl+C or external termination. Cleanup is registered before
  the subprocess is spawned so signals during startup still clean up.
- The reachability check polls the configured host:port with short timeouts
  until the gateway responds or a deadline is reached.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from types import FrameType
from typing import TYPE_CHECKING, TextIO, Union

if TYPE_CHECKING:
    from collections.abc import Callable

# Type alias for a signal handler: either a callable, SIG_DFL (0), or SIG_IGN (1).
SignalHandler = Union["Callable[[int, FrameType | None], object]", int, None]

logger = logging.getLogger(__name__)

# Maximum time to wait for the gateway subprocess to become reachable (seconds).
_DEFAULT_STARTUP_TIMEOUT = 15

# Polling interval for the reachability check (seconds).
_POLL_INTERVAL = 0.3


class GatewayAutoStartError(RuntimeError):
    """Raised when the gateway subprocess fails to start or become reachable."""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class GatewaySubprocess:
    """Manage a gateway proxy subprocess started from the ``run`` command.

    The subprocess is spawned lazily by :meth:`start_if_needed`. If the gateway
    is already reachable on the configured port, no subprocess is started and
    :attr:`managed` remains ``False``.

    Cleanup is registered via ``atexit`` and signal handlers so the child is
    terminated even if the parent exits via an exception or signal.
    """

    def __init__(
        self,
        port: int,
        db_path: str,
        host: str = "auto",
        log_file: str | None = None,
        startup_timeout: float = _DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        self.port = port
        self.db_path = db_path
        self.host = host
        self.log_file = log_file
        self.startup_timeout = startup_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._managed = False
        self._cleaned_up = False
        self._log_handle: TextIO | None = None
        self._prev_sigint: SignalHandler = None
        self._prev_sigterm: SignalHandler = None

    @property
    def managed(self) -> bool:
        """True if this instance started and owns the gateway subprocess."""
        return self._managed

    def _is_reachable(self, timeout: float = 1.0) -> bool:
        """Check if the gateway is reachable on 127.0.0.1 or the bridge IP."""
        from harness_evaluator.gateway.network import resolve_gateway_host

        probe_hosts: list[str] = []
        bridge_ip = resolve_gateway_host()
        if bridge_ip not in probe_hosts:
            probe_hosts.append(bridge_ip)
        if "127.0.0.1" not in probe_hosts:
            probe_hosts.append("127.0.0.1")

        for probe_host in probe_hosts:
            try:
                sock = socket.create_connection((probe_host, self.port), timeout=timeout)
                sock.close()
                return True
            except (ConnectionRefusedError, TimeoutError, OSError):
                continue
        return False

    def start_if_needed(self) -> bool:
        """Start the gateway subprocess if it is not already reachable.

        Returns ``True`` if a subprocess was started (caller owns it),
        ``False`` if the gateway was already reachable (caller should not
        manage it).

        Raises :class:`GatewayAutoStartError` if the subprocess fails to start
        or does not become reachable within the startup timeout.
        """
        if self._is_reachable():
            logger.debug("Gateway already reachable on port %d — not starting", self.port)
            return False

        # Register cleanup BEFORE spawning so that signals (Ctrl+C, SIGTERM)
        # during startup don't leave an orphaned gateway subprocess. The
        # cleanup handler is a no-op until _managed is set.
        self._register_cleanup()

        try:
            self._spawn()
            self._wait_for_reachable()
        except Exception:
            # If startup fails, clean up the subprocess and log handle
            # immediately rather than waiting for atexit.
            self.cleanup()
            raise
        return True

    def _spawn(self) -> None:
        """Spawn the gateway subprocess."""
        cmd = [
            sys.executable,
            "-m",
            "harness_evaluator.cli",
            "gateway",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--db",
            self.db_path,
        ]

        # Capture gateway output to a log file. We always use a log file
        # (defaulting to harness_evaluator_gateway.log in CWD) because:
        # - Inheriting stderr corrupts the Textual TUI terminal output.
        # - subprocess.STDOUT with stdout=DEVNULL sends stderr to DEVNULL too,
        #   silently discarding all gateway diagnostics.
        # - A log file ensures we can report the cause on startup failure.
        log_path = self.log_file or "harness_evaluator_gateway.log"
        # File handle must stay open for the subprocess lifetime —
        # cannot use a `with` block here.
        self._log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
        stdout_dest: int | TextIO = self._log_handle
        stderr_dest: int | TextIO = self._log_handle

        logger.info("Starting gateway subprocess: %s", " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=stdout_dest,
                stderr=stderr_dest,
                # Put the gateway in its own process group so we can clean up
                # the whole tree (aiohttp may spawn child tasks) on exit.
                start_new_session=True,
            )
        except OSError as exc:
            raise GatewayAutoStartError(
                f"Failed to spawn gateway subprocess: {exc}"
            ) from exc
        self._managed = True

    def _wait_for_reachable(self) -> None:
        """Poll until the gateway is reachable or the timeout expires."""
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            # Check if the subprocess died early
            if self._proc is not None and self._proc.poll() is not None:
                rc = self._proc.returncode
                log_tail = self._read_log_tail()
                msg = (
                    f"Gateway subprocess exited with code {rc} before becoming "
                    f"reachable on port {self.port}."
                )
                if log_tail:
                    msg += f"\nGateway log (last 10 lines):\n{log_tail}"
                elif self.log_file:
                    msg += f"\nCheck the gateway log: {self.log_file}"
                raise GatewayAutoStartError(msg, returncode=rc)
            if self._is_reachable(timeout=1.0):
                logger.info("Gateway subprocess is reachable on port %d", self.port)
                return
            time.sleep(_POLL_INTERVAL)

        # Timed out — kill the subprocess and report
        self._kill_subprocess()
        log_tail = self._read_log_tail()
        msg = (
            f"Gateway subprocess did not become reachable on port {self.port} "
            f"within {self.startup_timeout:.0f}s."
        )
        if log_tail:
            msg += f"\nGateway log (last 10 lines):\n{log_tail}"
        raise GatewayAutoStartError(msg)

    def _read_log_tail(self, n_lines: int = 10) -> str:
        """Read the last N lines from the gateway log file."""
        if self._log_handle is None:
            return ""
        try:
            self._log_handle.flush()
            self._log_handle.seek(0)
            lines = self._log_handle.readlines()
            return "".join(lines[-n_lines:])
        except OSError:
            return ""

    def _register_cleanup(self) -> None:
        """Register atexit and signal handlers to clean up the subprocess."""
        atexit.register(self.cleanup)

        # Install signal handlers that clean up before re-raising
        self._prev_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._prev_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame: FrameType | None) -> None:
        """Clean up the subprocess on signal, then restore the previous handler."""
        logger.debug("Received signal %d — cleaning up gateway subprocess", signum)
        self.cleanup()
        # Restore the previous handler and re-raise
        prev = self._prev_sigint if signum == signal.SIGINT else self._prev_sigterm
        signal.signal(signum, prev if callable(prev) else signal.SIG_DFL)
        # Re-raise by sending the signal to ourselves after restoring the handler
        os.kill(os.getpid(), signum)

    def _kill_subprocess(self) -> None:
        """Terminate the subprocess, escalating from SIGTERM to SIGKILL."""
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                # Send SIGTERM to the process group (start_new_session=True
                # means the child is its own session/group leader)
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Escalate to SIGKILL
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                    self._proc.wait(timeout=5)
        except (ProcessLookupError, OSError) as exc:
            logger.debug("Error killing gateway subprocess: %s", exc)
        finally:
            self._proc = None

    def cleanup(self) -> None:
        """Terminate the gateway subprocess if we started it."""
        if self._cleaned_up or not self._managed:
            return
        self._cleaned_up = True
        self._kill_subprocess()
        if self._log_handle is not None:
            with contextlib.suppress(OSError):
                self._log_handle.close()
            self._log_handle = None
        # Unregister atexit and restore signal handlers so repeated calls
        # and repeated run() invocations in one process don't stack handlers.
        with contextlib.suppress(ValueError):
            atexit.unregister(self.cleanup)
        if self._prev_sigint is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signal.SIGINT, self._prev_sigint)
            self._prev_sigint = None
        if self._prev_sigterm is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signal.SIGTERM, self._prev_sigterm)
            self._prev_sigterm = None
        logger.info("Gateway subprocess cleaned up")

    def __enter__(self) -> GatewaySubprocess:
        self.start_if_needed()
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()
