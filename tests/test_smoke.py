"""Smoke tests: verify the CLI commands work via Typer's CliRunner."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

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

    def test_dashboard_startup_prints_url_and_instructions(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`dashboard` prints the URL, DB info, and next steps before serving."""
        db = tmp_path / "missing.db"
        with patch("uvicorn.run") as mock_run:
            result = runner.invoke(
                app, ["dashboard", "--db", str(db), "--host", "127.0.0.1", "--port", "9999"]
            )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        out = result.stdout
        # The URL the user should open is printed.
        assert "http://127.0.0.1:9999" in out
        # The DB path is surfaced so a wrong --db is obvious.
        assert str(db) in out
        assert "not found" in out
        # The user is told how to stop the server.
        assert "Ctrl+C" in out
        # A helpful tip points at --db / `run`.
        assert "--db" in out

    def test_dashboard_startup_with_token_prints_login(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """With --token, the startup output directs the user to /login."""
        db = tmp_path / "missing.db"
        with patch("uvicorn.run") as mock_run:
            result = runner.invoke(
                app, ["dashboard", "--db", str(db), "--token", "secret"]
            )
        assert result.exit_code == 0
        out = result.stdout
        assert "/login" in out
        assert "Authorization: Bearer" in out
        # Access logs must be disabled when auth is on (no token in logs).
        assert mock_run.call_args.kwargs["access_log"] is False

    def test_dashboard_startup_with_populated_db(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A DB with runs reports the run count in the startup panel."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "results.db"
        ResultsStore(str(db))  # Create proper schema
        # Insert minimal rows with required NOT NULL columns.
        with sqlite3.connect(str(db)) as conn:
            conn.executemany(
                """INSERT INTO run_results
                   (run_name, cell_id, harness, model, task_id, track,
                    repeat, exit_class, success, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    ("run-a", "c1", "claude-code", "m", "t", "swe", 0, "success", 1.0, "now"),
                    ("run-a", "c2", "codex", "m", "t", "swe", 0, "success", 1.0, "now"),
                    ("run-b", "c3", "pi", "m", "t", "swe", 0, "success", 1.0, "now"),
                ],
            )
        with patch("uvicorn.run"):
            result = runner.invoke(app, ["dashboard", "--db", str(db)])
        assert result.exit_code == 0
        assert "2 runs available" in result.stdout


class TestResultsCommand:
    def test_results_lists_runs_when_no_name_given(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator results` with no arg lists available runs."""

        db = tmp_path / "results.db"
        with sqlite3.connect(str(db)) as conn:
            conn.executescript(
                """CREATE TABLE run_results (
                       run_name TEXT, cell_id TEXT PRIMARY KEY,
                       harness TEXT, model TEXT, task_id TEXT, track TEXT,
                       repeat INTEGER, exit_class TEXT, success REAL,
                       error_class TEXT, error_message TEXT,
                       input_tokens INTEGER, output_tokens INTEGER,
                       cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                       reasoning_tokens INTEGER, total_cost REAL,
                       latency_ms REAL, time_to_first_attempt_ms REAL,
                       num_api_calls INTEGER, num_tool_calls INTEGER,
                       diff TEXT, test_output TEXT,
                       harness_metadata TEXT, retry_count INTEGER
                   );
                   INSERT INTO run_results
                       (run_name, cell_id, harness, model, task_id, track,
                        repeat, exit_class, success, error_class, error_message,
                        input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, reasoning_tokens, total_cost,
                        latency_ms, time_to_first_attempt_ms, num_api_calls,
                        num_tool_calls, diff, test_output,
                        harness_metadata, retry_count)
                   VALUES
                       ('my-experiment', 'c1', 'claude-code', 'claude-sonnet-4',
                        'swe-001', 'swe', 0, 'success', 1.0, NULL, NULL,
                        100, 50, 0, 0, 0, 0.01, 5000, 100, 1, 0, NULL, NULL, NULL, 0),
                       ('my-experiment', 'c2', 'codex', 'gpt-4o',
                        'swe-001', 'swe', 0, 'failure', 0.0, 'test_fail', 'oops',
                        200, 80, 0, 0, 0, 0.02, 8000, 200, 2, 0, NULL, NULL, NULL, 0);
                """
            )
        result = runner.invoke(app, ["results", "--db", str(db)])
        assert result.exit_code == 0
        assert "my-experiment" in result.stdout
        assert "Available runs" in result.stdout

    def test_results_no_runs_prints_hint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator results` with empty DB prints a helpful hint."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "empty.db"
        # Let ResultsStore create the proper schema, then leave it empty.
        ResultsStore(str(db))
        result = runner.invoke(app, ["results", "--db", str(db)])
        assert result.exit_code == 1
        assert "No runs found" in result.stdout

    def test_results_wrong_name_lists_available(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator results <wrong>` lists available runs as a hint."""

        db = tmp_path / "results.db"
        with sqlite3.connect(str(db)) as conn:
            conn.executescript(
                """CREATE TABLE run_results (
                       run_name TEXT, cell_id TEXT PRIMARY KEY,
                       harness TEXT, model TEXT, task_id TEXT, track TEXT,
                       repeat INTEGER, exit_class TEXT, success REAL,
                       error_class TEXT, error_message TEXT,
                       input_tokens INTEGER, output_tokens INTEGER,
                       cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                       reasoning_tokens INTEGER, total_cost REAL,
                       latency_ms REAL, time_to_first_attempt_ms REAL,
                       num_api_calls INTEGER, num_tool_calls INTEGER,
                       diff TEXT, test_output TEXT,
                       harness_metadata TEXT, retry_count INTEGER
                   );
                   INSERT INTO run_results
                       (run_name, cell_id, harness, model, task_id, track,
                        repeat, exit_class, success, error_class, error_message,
                        input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, reasoning_tokens, total_cost,
                        latency_ms, time_to_first_attempt_ms, num_api_calls,
                        num_tool_calls, diff, test_output,
                        harness_metadata, retry_count)
                   VALUES
                       ('real-run', 'c1', 'claude-code', 'claude-sonnet-4',
                        'swe-001', 'swe', 0, 'success', 1.0, NULL, NULL,
                        100, 50, 0, 0, 0, 0.01, 5000, 100, 1, 0, NULL, NULL, NULL, 0);
                """
            )
        result = runner.invoke(app, ["results", "wrong-name", "--db", str(db)])
        assert result.exit_code == 1
        assert "No results found for run 'wrong-name'" in result.stdout
        assert "real-run" in result.stdout

    def test_results_missing_db_prints_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator results` with missing DB prints a clear error."""
        db = tmp_path / "nonexistent.db"
        assert not db.exists()
        result = runner.invoke(app, ["results", "--db", str(db)])
        assert result.exit_code == 1
        assert "Results DB not found" in result.stdout
        assert not db.exists(), "DB file should not be created"


class TestReportCommand:
    def test_report_lists_runs_when_no_name_given(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator report` with no arg lists available runs."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "results.db"
        ResultsStore(str(db))
        with sqlite3.connect(str(db)) as conn:
            conn.executemany(
                """INSERT INTO run_results
                   (run_name, cell_id, harness, model, task_id, track,
                    repeat, exit_class, success, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    ("my-experiment", "c1", "claude-code", "m", "t", "swe",
                     0, "success", 1.0, "now"),
                    ("my-experiment", "c2", "codex", "m", "t", "swe",
                     0, "success", 1.0, "now"),
                ],
            )
        result = runner.invoke(app, ["report", "--db", str(db)])
        assert result.exit_code == 0
        assert "my-experiment" in result.stdout
        assert "Available runs" in result.stdout

    def test_report_no_runs_prints_hint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator report` with empty DB prints a helpful hint."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "empty.db"
        ResultsStore(str(db))
        result = runner.invoke(app, ["report", "--db", str(db)])
        assert result.exit_code == 1
        assert "No runs found" in result.stdout

    def test_report_missing_db_prints_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator report` with missing DB prints a clear error."""
        db = tmp_path / "nonexistent.db"
        result = runner.invoke(app, ["report", "--db", str(db)])
        assert result.exit_code == 1
        assert "Results DB not found" in result.stdout
        assert not db.exists()

    def test_report_wrong_name_lists_available(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator report <wrong>` lists available runs as a hint."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "results.db"
        ResultsStore(str(db))
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                """INSERT INTO run_results
                   (run_name, cell_id, harness, model, task_id, track,
                    repeat, exit_class, success, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("real-run", "c1", "claude-code", "m", "t", "swe",
                 0, "success", 1.0, "now"),
            )
        result = runner.invoke(app, ["report", "wrong-name", "--db", str(db)])
        assert result.exit_code == 1
        assert "real-run" in result.stdout


class TestStatsCommand:
    def test_stats_lists_runs_when_no_name_given(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator stats` with no arg lists available runs."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "results.db"
        ResultsStore(str(db))
        with sqlite3.connect(str(db)) as conn:
            conn.executemany(
                """INSERT INTO run_results
                   (run_name, cell_id, harness, model, task_id, track,
                    repeat, exit_class, success, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    ("my-experiment", "c1", "claude-code", "m", "t", "swe",
                     0, "success", 1.0, "now"),
                    ("my-experiment", "c2", "codex", "m", "t", "swe",
                     0, "success", 1.0, "now"),
                ],
            )
        result = runner.invoke(app, ["stats", "--db", str(db)])
        assert result.exit_code == 0
        assert "my-experiment" in result.stdout
        assert "Available runs" in result.stdout

    def test_stats_no_runs_prints_hint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator stats` with empty DB prints a helpful hint."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "empty.db"
        ResultsStore(str(db))
        result = runner.invoke(app, ["stats", "--db", str(db)])
        assert result.exit_code == 1
        assert "No runs found" in result.stdout

    def test_stats_missing_db_prints_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator stats` with missing DB prints a clear error."""
        db = tmp_path / "nonexistent.db"
        result = runner.invoke(app, ["stats", "--db", str(db)])
        assert result.exit_code == 1
        assert "Results DB not found" in result.stdout
        assert not db.exists()

    def test_stats_wrong_name_lists_available(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`harness-evaluator stats <wrong>` lists available runs as a hint."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "results.db"
        ResultsStore(str(db))
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                """INSERT INTO run_results
                   (run_name, cell_id, harness, model, task_id, track,
                    repeat, exit_class, success, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("real-run", "c1", "claude-code", "m", "t", "swe",
                 0, "success", 1.0, "now"),
            )
        result = runner.invoke(app, ["stats", "wrong-name", "--db", str(db)])
        assert result.exit_code == 1
        assert "No results found for run 'wrong-name'" in result.stdout
        assert "real-run" in result.stdout


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
