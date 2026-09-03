"""Tests for container liveness checking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from harness_evaluator.runner.docker import check_container_liveness


class TestCheckContainerLiveness:
    def test_returns_status_on_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "running\n"
        with patch("harness_evaluator.runner.docker.subprocess.run", return_value=mock_result):
            status = check_container_liveness("h1__m1__t1__r0")
        assert status == "running"

    def test_returns_none_on_nonzero_exit(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("harness_evaluator.runner.docker.subprocess.run", return_value=mock_result):
            status = check_container_liveness("nonexistent_cell")
        assert status is None

    def test_returns_none_on_timeout(self) -> None:
        import subprocess

        with patch(
            "harness_evaluator.runner.docker.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=2),
        ):
            status = check_container_liveness("h1__m1__t1__r0")
        assert status is None

    def test_returns_none_on_exception(self) -> None:
        with patch(
            "harness_evaluator.runner.docker.subprocess.run",
            side_effect=FileNotFoundError("docker not found"),
        ):
            status = check_container_liveness("h1__m1__t1__r0")
        assert status is None

    def test_sanitizes_cell_id_for_container_name(self) -> None:
        """The cell ID is sanitized to a valid Docker container name."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "exited\n"
        with patch(
            "harness_evaluator.runner.docker.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            check_container_liveness("h1__m1__t1__r0")
            call_args = mock_run.call_args
            # The container name should be in the inspect args
            args = call_args[0][0]
            assert "harness-evaluator-h1__m1__t1__r0" in args
