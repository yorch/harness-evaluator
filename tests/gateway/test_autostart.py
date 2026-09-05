"""Tests for the gateway auto-start subprocess manager."""

from __future__ import annotations

import socket
import threading
from unittest.mock import patch

import pytest

from harness_evaluator.gateway.autostart import (
    GatewayAutoStartError,
    GatewaySubprocess,
)


def _find_free_port() -> int:
    """Find a free TCP port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_dummy_server(port: int, ready_event: threading.Event) -> threading.Thread:
    """Start a dummy TCP server that accepts connections on the given port."""

    def _serve() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        srv.settimeout(10)
        ready_event.set()
        try:
            conn, _ = srv.accept()
            conn.close()
        except (TimeoutError, OSError):
            pass
        srv.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return t


class TestGatewaySubprocessReachability:
    """Tests for the _is_reachable check."""

    def test_is_reachable_returns_true_for_open_port(self) -> None:
        port = _find_free_port()
        ready = threading.Event()
        _start_dummy_server(port, ready)
        ready.wait(timeout=5)

        gw = GatewaySubprocess(port=port, db_path=":memory:")
        # The dummy server listens on 127.0.0.1, so _is_reachable should find it
        assert gw._is_reachable(timeout=1.0)

    def test_is_reachable_returns_false_for_closed_port(self) -> None:
        port = _find_free_port()
        # Don't start any server
        gw = GatewaySubprocess(port=port, db_path=":memory:")
        assert not gw._is_reachable(timeout=0.5)


class TestGatewaySubprocessStartIfNeeded:
    """Tests for start_if_needed — the main entry point."""

    def test_returns_false_when_gateway_already_reachable(self) -> None:
        """If a gateway is already running, no subprocess is started."""
        port = _find_free_port()
        ready = threading.Event()
        _start_dummy_server(port, ready)
        ready.wait(timeout=5)

        gw = GatewaySubprocess(port=port, db_path=":memory:")
        result = gw.start_if_needed()
        assert result is False
        assert gw.managed is False

    def test_raises_when_subprocess_dies_immediately(self) -> None:
        """If the spawned subprocess exits before becoming reachable, raise."""
        port = _find_free_port()

        gw = GatewaySubprocess(port=port, db_path=":memory:", startup_timeout=5)

        # Mock _spawn to set _proc to a fake that immediately reports as dead
        def fake_spawn(self: GatewaySubprocess) -> None:
            class FakeProc:
                def poll(self) -> int:
                    return 1  # Process exited with code 1

                @property
                def returncode(self) -> int:
                    return 1

                @property
                def pid(self) -> int:
                    return 99999

            self._proc = FakeProc()  # type: ignore[assignment]
            self._managed = True

        with (
            patch.object(GatewaySubprocess, "_spawn", fake_spawn),
            pytest.raises(GatewayAutoStartError) as exc_info,
        ):
            gw.start_if_needed()

        assert "exited with code 1" in str(exc_info.value)
        assert exc_info.value.returncode == 1

    def test_raises_when_timeout_exceeded(self) -> None:
        """If the gateway never becomes reachable, raise after timeout."""
        port = _find_free_port()

        gw = GatewaySubprocess(
            port=port, db_path=":memory:", startup_timeout=1.0
        )

        # Mock _spawn to set _proc to a fake that stays alive but never
        # becomes reachable (no server on the port)
        def fake_spawn(self: GatewaySubprocess) -> None:
            class FakeProc:
                def poll(self) -> int | None:
                    return None  # Still running

                @property
                def returncode(self) -> int | None:
                    return None

                @property
                def pid(self) -> int:
                    return 99999

            self._proc = FakeProc()  # type: ignore[assignment]
            self._managed = True

        def fake_kill(self: GatewaySubprocess) -> None:
            self._proc = None

        with (
            patch.object(GatewaySubprocess, "_spawn", fake_spawn),
            patch.object(GatewaySubprocess, "_kill_subprocess", fake_kill),
            pytest.raises(GatewayAutoStartError) as exc_info,
        ):
            gw.start_if_needed()

        assert "did not become reachable" in str(exc_info.value)


class TestGatewaySubprocessCleanup:
    """Tests for cleanup behavior."""

    def test_cleanup_noop_when_not_managed(self) -> None:
        """cleanup() should be a no-op if we didn't start a subprocess."""
        gw = GatewaySubprocess(port=12345, db_path=":memory:")
        gw.cleanup()  # Should not raise

    def test_cleanup_is_idempotent(self) -> None:
        """cleanup() can be called multiple times safely."""
        gw = GatewaySubprocess(port=12345, db_path=":memory:")
        gw._managed = True
        gw._cleaned_up = True  # Already cleaned up
        gw.cleanup()  # Should not raise

    def test_cleanup_kills_subprocess(self) -> None:
        """cleanup() terminates the subprocess if it's still running."""
        gw = GatewaySubprocess(port=12345, db_path=":memory:")
        gw._managed = True

        killed = threading.Event()

        def fake_kill(self: GatewaySubprocess) -> None:
            killed.set()
            self._proc = None

        with patch.object(GatewaySubprocess, "_kill_subprocess", fake_kill):
            gw.cleanup()

        assert killed.is_set()


class TestGatewaySubprocessContextManager:
    """Tests for the context manager protocol."""

    def test_context_manager_cleans_up_on_exit(self) -> None:
        """Using `with` should clean up on exit."""
        port = _find_free_port()
        ready = threading.Event()
        _start_dummy_server(port, ready)
        ready.wait(timeout=5)

        gw = GatewaySubprocess(port=port, db_path=":memory:")
        with gw:
            pass  # Gateway was already reachable, no subprocess started
        # Should not raise on exit


class TestGatewaySubprocessLogHandling:
    """Tests for log file handling."""

    def test_log_file_opened_and_closed(self, tmp_path: object) -> None:
        """When a log file is specified, it's opened and closed on cleanup."""
        import pathlib

        log_path = str(pathlib.Path(str(tmp_path)) / "gw.log")
        gw = GatewaySubprocess(port=12345, db_path=":memory:", log_file=log_path)
        gw._managed = True

        # Simulate spawn opening the log file
        gw._log_handle = open(log_path, "w")  # noqa: SIM115
        assert gw._log_handle is not None

        gw.cleanup()
        assert gw._log_handle is None
        # File should exist
        assert pathlib.Path(log_path).exists()
