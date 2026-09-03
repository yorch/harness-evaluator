"""Tests for gateway network helpers (Docker bridge IP detection)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from harness_evaluator.cli import app
from harness_evaluator.gateway.network import (
    _LOOPBACK_FALLBACK,
    _validate_ip,
    format_host_for_url,
    resolve_gateway_host,
)

runner = CliRunner()


class TestValidateIp:
    """Tests for _validate_ip()."""

    def test_accepts_valid_ipv4(self) -> None:
        assert _validate_ip("172.17.0.1") == "172.17.0.1"

    def test_accepts_valid_ipv6(self) -> None:
        assert _validate_ip("::1") == "::1"

    def test_strips_whitespace(self) -> None:
        assert _validate_ip("  172.17.0.1\n") == "172.17.0.1"

    def test_rejects_wildcard_ipv4(self) -> None:
        """0.0.0.0 must be rejected — the whole point is avoiding all-interfaces."""
        assert _validate_ip("0.0.0.0") is None

    def test_rejects_wildcard_ipv6(self) -> None:
        assert _validate_ip("::") is None

    def test_rejects_empty(self) -> None:
        assert _validate_ip("") is None

    def test_rejects_whitespace_only(self) -> None:
        assert _validate_ip("  \n") is None

    def test_rejects_non_ip_string(self) -> None:
        assert _validate_ip("not-an-ip") is None

    def test_rejects_hostname(self) -> None:
        assert _validate_ip("localhost") is None

    def test_rejects_multicast(self) -> None:
        assert _validate_ip("224.0.0.1") is None


class TestFormatHostForUrl:
    """Tests for format_host_for_url()."""

    def test_ipv4_unchanged(self) -> None:
        assert format_host_for_url("172.17.0.1") == "172.17.0.1"

    def test_ipv6_gets_brackets(self) -> None:
        assert format_host_for_url("::1") == "[::1]"

    def test_ipv6_full_address(self) -> None:
        assert format_host_for_url("fd00::1") == "[fd00::1]"

    def test_hostname_unchanged(self) -> None:
        assert format_host_for_url("host.docker.internal") == "host.docker.internal"

    def test_localhost_unchanged(self) -> None:
        assert format_host_for_url("localhost") == "localhost"


class TestResolveGatewayHost:
    """Tests for resolve_gateway_host()."""

    def test_returns_inspected_bridge_ip(self) -> None:
        """When `docker network inspect` succeeds and IP is bindable, it's used."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "172.17.0.1\n"
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                return_value=mock_result,
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=True,
            ),
        ):
            assert resolve_gateway_host() == "172.17.0.1"

    def test_returns_custom_bridge_ip(self) -> None:
        """A non-default bridge subnet (e.g. rootless Docker) is honored."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "10.23.0.1\n"
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                return_value=mock_result,
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=True,
            ),
        ):
            assert resolve_gateway_host() == "10.23.0.1"

    def test_falls_back_to_loopback_when_not_bindable(self) -> None:
        """Docker Desktop: bridge IP detected but not bindable → loopback."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "172.17.0.1\n"
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                return_value=mock_result,
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            assert resolve_gateway_host() == _LOOPBACK_FALLBACK

    def test_falls_back_when_docker_not_found(self) -> None:
        """When docker is not installed, falls back to loopback (not bindable)."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                side_effect=FileNotFoundError("docker not found"),
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            assert resolve_gateway_host() == _LOOPBACK_FALLBACK

    def test_falls_back_on_inspect_failure(self) -> None:
        """When `docker network inspect` exits non-zero, falls back."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                return_value=mock_result,
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            assert resolve_gateway_host() == _LOOPBACK_FALLBACK

    def test_rejects_wildcard_from_docker(self) -> None:
        """If docker returns 0.0.0.0, it is rejected and we fall back."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "0.0.0.0\n"
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                return_value=mock_result,
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            # 0.0.0.0 is rejected by _validate_ip, so _detect returns None,
            # and we fall back to loopback since 172.17.0.1 isn't bindable
            assert resolve_gateway_host() == _LOOPBACK_FALLBACK

    def test_rejects_non_ip_from_docker(self) -> None:
        """If docker returns junk text, it is rejected and we fall back."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "some-malicious-text\n"
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                return_value=mock_result,
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            assert resolve_gateway_host() == _LOOPBACK_FALLBACK

    def test_falls_back_on_timeout(self) -> None:
        """When docker inspect times out, falls back."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            assert resolve_gateway_host() == _LOOPBACK_FALLBACK

    def test_never_raises(self) -> None:
        """resolve_gateway_host must never raise — it returns a fallback."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run",
                side_effect=OSError("unexpected"),
            ),
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
        ):
            result = resolve_gateway_host()
            assert result == _LOOPBACK_FALLBACK


class TestCliGatewayAutoHost:
    """Tests for the CLI `gateway` command with --host auto."""

    def test_auto_host_resolves_to_bridge_ip(self, tmp_path: Path) -> None:
        """The gateway command resolves 'auto' to the Docker bridge IP."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run"
            ) as mock_run,
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=True,
            ),
            patch("harness_evaluator.gateway.proxy.run_proxy") as mock_proxy,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "172.17.0.1\n"
            mock_run.return_value = mock_result

            runner.invoke(
                app,
                [
                    "gateway",
                    "--port",
                    "8877",
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            # run_proxy was called with the resolved IP, not "auto"
            mock_proxy.assert_called_once()
            assert mock_proxy.call_args.kwargs["host"] == "172.17.0.1"

    def test_explicit_host_not_resolved(self, tmp_path: Path) -> None:
        """An explicit --host is used literally, not resolved."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run"
            ) as mock_run,
            patch("harness_evaluator.gateway.proxy.run_proxy") as mock_proxy,
        ):
            runner.invoke(
                app,
                [
                    "gateway",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8877",
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            # docker network inspect was NOT called
            mock_run.assert_not_called()
            mock_proxy.assert_called_once()
            assert mock_proxy.call_args.kwargs["host"] == "127.0.0.1"

    def test_auto_host_prints_detection_message(self, tmp_path: Path) -> None:
        """The auto-detection info line is shown when --host is auto."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run"
            ) as mock_run,
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=True,
            ),
            patch("harness_evaluator.gateway.proxy.run_proxy"),
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "172.17.0.1\n"
            mock_run.return_value = mock_result

            result = runner.invoke(
                app,
                [
                    "gateway",
                    "--port",
                    "8877",
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            assert "Auto-detected Docker bridge gateway" in result.stdout
            assert "172.17.0.1" in result.stdout

    def test_auto_host_falls_back_to_loopback(self, tmp_path: Path) -> None:
        """When bridge IP is not bindable, auto falls back to 127.0.0.1."""
        with (
            patch(
                "harness_evaluator.gateway.network.subprocess.run"
            ) as mock_run,
            patch(
                "harness_evaluator.gateway.network._is_bindable",
                return_value=False,
            ),
            patch("harness_evaluator.gateway.proxy.run_proxy") as mock_proxy,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "172.17.0.1\n"
            mock_run.return_value = mock_result

            result = runner.invoke(
                app,
                [
                    "gateway",
                    "--port",
                    "8877",
                    "--db",
                    str(tmp_path / "gw.db"),
                ],
            )
            mock_proxy.assert_called_once()
            assert mock_proxy.call_args.kwargs["host"] == _LOOPBACK_FALLBACK
            assert _LOOPBACK_FALLBACK in result.stdout
