"""Smoke tests: verify the CLI commands work via Typer's CliRunner."""

from __future__ import annotations

from typer.testing import CliRunner

from harness_evaluator.cli import app

runner = CliRunner()


class TestAdaptersCommand:
    def test_adapters_lists_all_five(self) -> None:
        """`harness-evaluator adapters` should list all 5 registered adapters."""
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


class TestInitCommand:
    def test_init_generates_runnable_config(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator init` writes a config that loads and builds a matrix."""
        cfg_path = tmp_path / "harness-evaluator.yaml"
        result = runner.invoke(app, ["init", "--filename", str(cfg_path)])
        assert result.exit_code == 0
        assert cfg_path.exists()

        from harness_evaluator.orchestrator.config import RunConfig

        cfg = RunConfig.from_yaml(str(cfg_path))
        # Uses the bundled task library + published image by default.
        assert "ghcr.io/yorch/harness-evaluator-runner" in cfg.docker_image
        assert len(cfg.build_matrix()) >= 1

    def test_init_refuses_overwrite_without_force(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg_path = tmp_path / "harness-evaluator.yaml"
        cfg_path.write_text("existing")
        result = runner.invoke(app, ["init", "--filename", str(cfg_path)])
        assert result.exit_code == 1
        assert cfg_path.read_text() == "existing"
