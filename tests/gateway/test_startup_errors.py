"""Tests for gateway startup error handling (port in use, permission denied, etc.)."""

from __future__ import annotations

import errno
import socket
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from harness_evaluator.cli import app
from harness_evaluator.gateway.proxy import (
    GatewayStartupError,
    _check_port_available,
    _convert_oserror_to_startup_error,
    _format_eaddrnotavail_error,
)

runner = CliRunner()


def _occupy_port(host: str = "127.0.0.1") -> tuple[int, socket.socket]:
    """Bind a socket to a random free port and keep it occupied.

    Returns (port, socket) — caller must close the socket to release the port.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    return port, sock


class TestCheckPortAvailable:
    def test_raises_for_in_use_port(self) -> None:
        """_check_port_available raises GatewayStartupError for a taken port."""
        port, sock = _occupy_port()
        try:
            with pytest.raises(GatewayStartupError) as exc_info:
                _check_port_available("127.0.0.1", port)
            assert exc_info.value.errno == errno.EADDRINUSE
            assert str(port) in exc_info.value.message
            assert "already in use" in exc_info.value.message
        finally:
            sock.close()

    def test_message_includes_port_and_suggestions(self) -> None:
        """Error message includes the port number and actionable suggestions."""
        port, sock = _occupy_port()
        try:
            with pytest.raises(GatewayStartupError) as exc_info:
                _check_port_available("127.0.0.1", port)
            msg = exc_info.value.message
            assert str(port) in msg
            assert "--port" in msg
            assert "lsof" in msg or "netstat" in msg
        finally:
            sock.close()

    def test_passes_for_free_port(self) -> None:
        """_check_port_available does not raise for a free port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        _check_port_available("127.0.0.1", port)

    def test_rejects_port_zero(self) -> None:
        """Port 0 is rejected — it would bind a random ephemeral port."""
        with pytest.raises(GatewayStartupError) as exc_info:
            _check_port_available("127.0.0.1", 0)
        assert "Invalid port" in exc_info.value.message
        assert exc_info.value.port == 0

    def test_rejects_negative_port(self) -> None:
        """Negative ports are rejected."""
        with pytest.raises(GatewayStartupError) as exc_info:
            _check_port_available("127.0.0.1", -1)
        assert "Invalid port" in exc_info.value.message

    def test_rejects_out_of_range_port(self) -> None:
        """Ports > 65535 are rejected."""
        with pytest.raises(GatewayStartupError) as exc_info:
            _check_port_available("127.0.0.1", 70000)
        assert "Invalid port" in exc_info.value.message

    def test_rejects_unresolvable_host(self) -> None:
        """An unresolvable hostname raises GatewayStartupError."""
        with pytest.raises(GatewayStartupError) as exc_info:
            _check_port_available("nonexistent-host-xyz.invalid", 8877)
        assert "Cannot resolve host" in exc_info.value.message

    def test_carries_host_and_port_in_error(self) -> None:
        """GatewayStartupError carries host and port for programmatic access."""
        port, sock = _occupy_port()
        try:
            with pytest.raises(GatewayStartupError) as exc_info:
                _check_port_available("127.0.0.1", port)
            assert exc_info.value.host == "127.0.0.1"
            assert exc_info.value.port == port
        finally:
            sock.close()

    def test_supports_ipv6_loopback(self) -> None:
        """_check_port_available works with IPv6 loopback (::1)."""
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.bind(("::1", 0))
            port = sock.getsockname()[1]
            sock.close()
            _check_port_available("::1", port)
        except OSError:
            # IPv6 not available on this platform — skip
            sock.close()
            pytest.skip("IPv6 not available on this platform")

    def test_supports_wildcard_host(self) -> None:
        """_check_port_available works with 0.0.0.0 (all interfaces)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        sock.close()
        _check_port_available("0.0.0.0", port)


class TestConvertOSError:
    """Test the _convert_oserror_to_startup_error helper."""

    def test_converts_eaddrinuse(self) -> None:
        exc = OSError(errno.EADDRINUSE, "Address already in use")
        result = _convert_oserror_to_startup_error(exc, "127.0.0.1", 8877)
        assert result.errno == errno.EADDRINUSE
        assert "8877" in result.message
        assert "already in use" in result.message
        assert result.host == "127.0.0.1"
        assert result.port == 8877

    def test_converts_eacces(self) -> None:
        exc = OSError(errno.EACCES, "Permission denied")
        result = _convert_oserror_to_startup_error(exc, "127.0.0.1", 80)
        assert result.errno == errno.EACCES
        assert "Permission denied" in result.message
        assert result.port == 80

    def test_converts_unknown_oserror(self) -> None:
        exc = OSError(999, "Unknown error")
        result = _convert_oserror_to_startup_error(exc, "0.0.0.0", 1234)
        assert result.errno == 999
        assert "Cannot bind" in result.message
        assert result.host == "0.0.0.0"
        assert result.port == 1234


class TestRunProxyPortInUse:
    """Test that run_proxy raises GatewayStartupError when port is taken."""

    def test_run_proxy_raises_startup_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """run_proxy raises GatewayStartupError, not a bare OSError."""
        from harness_evaluator.gateway.proxy import run_proxy

        port, sock = _occupy_port()
        try:
            with pytest.raises(GatewayStartupError) as exc_info:
                run_proxy(
                    host="127.0.0.1",
                    port=port,
                    db_path=str(tmp_path / "test.db"),
                )
            assert exc_info.value.errno == errno.EADDRINUSE
        finally:
            sock.close()

    def test_run_proxy_raises_for_invalid_port(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """run_proxy raises GatewayStartupError for port 0."""
        from harness_evaluator.gateway.proxy import run_proxy

        with pytest.raises(GatewayStartupError) as exc_info:
            run_proxy(
                host="127.0.0.1",
                port=0,
                db_path=str(tmp_path / "test.db"),
            )
        assert "Invalid port" in exc_info.value.message


class TestCliGatewayPortInUse:
    """Test that the CLI `gateway` command exits cleanly with a friendly message."""

    def test_cli_gateway_port_in_use_exits_1(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator gateway` on a taken port exits 1 with a clear message."""
        port, sock = _occupy_port()
        try:
            result = runner.invoke(
                app,
                [
                    "gateway",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            assert result.exit_code == 1
            assert "Traceback" not in result.stdout
            assert "Cannot start gateway" in result.stdout
            assert str(port) in result.stdout
            assert "already in use" in result.stdout
        finally:
            sock.close()

    def test_cli_gateway_port_in_use_suggests_alternative(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The error message suggests using a different port."""
        port, sock = _occupy_port()
        try:
            result = runner.invoke(
                app,
                [
                    "gateway",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            assert result.exit_code == 1
            assert f"--port {port + 1}" in result.stdout
        finally:
            sock.close()

    def test_cli_gateway_no_traceback_on_port_in_use(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """No Python traceback is shown to the user when the port is taken."""
        port, sock = _occupy_port()
        try:
            result = runner.invoke(
                app,
                [
                    "gateway",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            assert result.exit_code == 1
            assert "OSError" not in result.stdout
            assert "Errno" not in result.stdout
        finally:
            sock.close()

    def test_cli_gateway_invalid_port_exits_1(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator gateway --port 0` exits 1 with invalid port message."""
        result = runner.invoke(
            app,
            [
                "gateway",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--db",
                str(tmp_path / "gw.db"),
            ],
        )
        assert result.exit_code == 1
        assert "Invalid port" in result.stdout
        assert "Traceback" not in result.stdout

    def test_cli_gateway_prints_actual_host_port(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The startup banner uses the actual --host and --port values."""
        port, sock = _occupy_port()
        try:
            result = runner.invoke(
                app,
                [
                    "gateway",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            # Even though it fails (port in use), the banner should show
            # the actual port, not the hardcoded 8877
            assert str(port) in result.stdout
            assert f"http://127.0.0.1:{port}" in result.stdout
        finally:
            sock.close()


class TestGatewayStartupError:
    """Test the GatewayStartupError exception itself."""

    def test_carries_errno(self) -> None:
        err = GatewayStartupError("test", errno=errno.EADDRINUSE)
        assert err.errno == errno.EADDRINUSE
        assert err.message == "test"

    def test_errno_defaults_none(self) -> None:
        err = GatewayStartupError("test")
        assert err.errno is None

    def test_carries_host_and_port(self) -> None:
        err = GatewayStartupError("test", host="0.0.0.0", port=8080)
        assert err.host == "0.0.0.0"
        assert err.port == 8080

    def test_host_port_default_none(self) -> None:
        err = GatewayStartupError("test")
        assert err.host is None
        assert err.port is None

    def test_is_exception_subclass(self) -> None:
        err = GatewayStartupError("test")
        assert isinstance(err, Exception)


class TestEaddrnotavailError:
    """Tests for EADDRNOTAVAIL handling (e.g. Docker Desktop bridge IP)."""

    def test_format_mentions_docker_desktop(self) -> None:
        """The error message mentions Docker Desktop and suggests fixes."""
        msg = _format_eaddrnotavail_error("172.17.0.1", 8877)
        assert "172.17.0.1" in msg
        assert "Docker Desktop" in msg
        assert "--host 0.0.0.0" in msg
        assert "--host 127.0.0.1" in msg

    def test_convert_oserror_eaddrnotavail(self) -> None:
        """_convert_oserror_to_startup_error handles EADDRNOTAVAIL."""
        exc = OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        result = _convert_oserror_to_startup_error(exc, "172.17.0.1", 8877)
        assert result.errno == errno.EADDRNOTAVAIL
        assert "Docker Desktop" in result.message
        assert result.host == "172.17.0.1"
        assert result.port == 8877

    def test_check_port_available_raises_eaddrnotavail_error(self) -> None:
        """_check_port_available raises a friendly error for EADDRNOTAVAIL."""
        with patch(
            "harness_evaluator.gateway.proxy.socket.getaddrinfo"
        ) as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("172.17.0.1", 8877))
            ]
            with patch(
                "harness_evaluator.gateway.proxy.socket.socket"
            ) as mock_socket_cls:
                mock_sock = MagicMock()
                mock_sock.bind.side_effect = OSError(
                    errno.EADDRNOTAVAIL, "Cannot assign requested address"
                )
                mock_socket_cls.return_value = mock_sock

                with pytest.raises(GatewayStartupError) as exc_info:
                    _check_port_available("172.17.0.1", 8877)
                assert exc_info.value.errno == errno.EADDRNOTAVAIL
                assert "Docker Desktop" in exc_info.value.message
