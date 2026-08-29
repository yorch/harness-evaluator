"""Smoke tests: verify the CLI commands work via Typer's CliRunner."""

from __future__ import annotations

from typer.testing import CliRunner

from heval.cli import app

runner = CliRunner()


class TestAdaptersCommand:
    def test_adapters_lists_all_five(self) -> None:
        """`heval adapters` should list all 5 registered adapters."""
        result = runner.invoke(app, ["adapters"])
        assert result.exit_code == 0
        # All five adapters should appear in the output.
        assert "opencode" in result.stdout
        assert "claude-code" in result.stdout
        assert "codex" in result.stdout
        assert "pi" in result.stdout
        assert "omp" in result.stdout


class TestHelpCommands:
    """Each subcommand should respond to --help without error."""

    def test_run_help(self) -> None:
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "config" in result.stdout.lower()

    def test_gateway_help(self) -> None:
        result = runner.invoke(app, ["gateway", "--help"])
        assert result.exit_code == 0
        assert "port" in result.stdout.lower()

    def test_stats_help(self) -> None:
        result = runner.invoke(app, ["stats", "--help"])
        assert result.exit_code == 0
        assert "run_name" in result.stdout.lower()

    def test_dashboard_help(self) -> None:
        result = runner.invoke(app, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "port" in result.stdout.lower()
