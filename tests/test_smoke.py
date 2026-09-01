"""Smoke tests: verify the CLI commands work via Typer's CliRunner."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from typer.testing import CliRunner

from harness_evaluator.cli import app
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)

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
        # --no-tui may be wrapped or ANSI-styled in CI; check the help text.
        assert "tui" in result.stdout.lower()

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

    def test_results_shows_error_columns(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`results` table includes error_class and error_message columns."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        db = tmp_path / "results.db"
        store = ResultsStore(str(db))
        cell = RunCell(
            run_name="err-run",
            harness=HarnessSpec(name="claude-code", adapter="claude-code"),
            model=ModelSpec(name="claude-sonnet-4", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="swe-001", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="fail", success=0.0,
            error_class="crash", error_message="Segmentation fault in harness",
            total_cost=0.01, latency_ms=5000, num_api_calls=1,
        )
        # Use a wide terminal so Rich doesn't truncate column headers.
        result = runner.invoke(
            app, ["results", "err-run", "--db", str(db)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0
        assert "Error Class" in result.stdout
        assert "Error Message" in result.stdout
        assert "crash" in result.stdout
        assert "Segmentation fault in harness" in result.stdout

    def test_results_truncates_long_error_message(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Long error messages are truncated with ellipsis in the results table."""
        from harness_evaluator.orchestrator.results_store import ResultsStore

        long_msg = "A" * 100
        db = tmp_path / "results.db"
        store = ResultsStore(str(db))
        cell = RunCell(
            run_name="trunc-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="fail", success=0.0,
            error_class="err", error_message=long_msg,
            total_cost=0, latency_ms=0, num_api_calls=0,
        )
        result = runner.invoke(
            app, ["results", "trunc-run", "--db", str(db)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0
        # The full 100-char message should NOT appear (truncated to 60+…).
        assert long_msg not in result.stdout
        assert "A" * 60 + "\u2026" in result.stdout


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


class TestCalibrateCommand:
    def test_calibrate_surfaces_judge_errors(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`calibrate` should print judge errors so API failures aren't silent."""
        cal_file = tmp_path / "cal.json"
        cal_file.write_text(
            '{"anchors": [{"name": "perfect", "diff": "x", '
            '"expected_scores": {"correctness": 5}, "expected_success": 1.0}]}'
        )

        fake_result = {
            "judge_version": "v1.0",
            "num_anchors": 1,
            "results": [
                {
                    "name": "perfect",
                    "expected_success": 1.0,
                    "actual_success": 0.0,
                    "success_error": 1.0,
                    "score_errors": {"correctness": 5},
                    "judge_error": "HTTP 401: API key is invalid.",
                }
            ],
            "mean_absolute_error": 1.0,
            "drift_detected": True,
            "reliable": False,
        }

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"}):
            import asyncio

            from harness_evaluator.evaluator.open_ended import CalibrationSet

            with (
                patch.object(CalibrationSet, "calibrate", return_value=fake_result),
                patch.object(asyncio, "run", return_value=fake_result),
            ):
                result = runner.invoke(
                    app,
                    ["calibrate", "--calibration-file", str(cal_file)],
                )

        assert result.exit_code == 0
        assert "judge error" in result.stdout
        assert "HTTP 401" in result.stdout
