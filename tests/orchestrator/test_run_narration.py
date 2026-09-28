"""Tests for the orchestrator's run narration.

A run used to emit three log lines for its entire duration, so a long cell
was indistinguishable from a hang and a failing cell said nothing about why.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
import yaml

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunConfig,
)
from harness_evaluator.orchestrator.engine import (
    Orchestrator,
    RetryableError,
    cell_label,
    truncate_message,
)
from harness_evaluator.orchestrator.results_store import ResultsStore


@pytest.fixture
def config(tmp_path):
    (tmp_path / "task.yaml").write_text(
        yaml.dump(
            {
                "tasks": [
                    {
                        "id": "t1",
                        "name": "Task 1",
                        "track": "swe",
                        "task_prompt": "Fix bug",
                        "test_command": "true",
                    }
                ]
            }
        )
    )
    return RunConfig(
        name="narration-test",
        harnesses=[HarnessSpec(name="h1", adapter="opencode")],
        models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
        tasks=["*"],
        task_library_path=str(tmp_path),
        repeats=2,
    )


@pytest.fixture
def store(tmp_path):
    return ResultsStore(str(tmp_path / "results.db"))


def _result(**overrides):
    base = {
        "exit_class": "pass",
        "success": 1.0,
        "usage": TokenUsage(input_tokens=100, output_tokens=50),
        "total_cost": 0.02,
        "latency_ms": 1000.0,
        "num_api_calls": 7,
    }
    base.update(overrides)
    return base


class TestHelpers:
    def test_cell_label_is_human_readable(self, config):
        cell = config.build_matrix()[0]
        assert cell_label(cell) == "h1 | m1 | t1 | rep 0"

    def test_truncate_message_collapses_and_bounds(self):
        assert truncate_message("a\n  b\tc") == "a b c"
        assert truncate_message("x" * 50, limit=10) == "x" * 9 + "\u2026"

    def test_truncate_message_redacts_and_strips_control_chars(self):
        """Exception text can carry raw setup-script or docker output."""
        msg = truncate_message(
            "setup failed: \x1b[31mANTHROPIC_API_KEY=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA\x1b[0m"
        )
        assert "sk-ant-api03" not in msg
        assert "[REDACTED]" in msg
        assert "\x1b" not in msg


class TestCellNarration:
    async def test_start_and_pass_are_logged_with_ordinal_and_stats(
        self, config, store, caplog
    ):
        async def run_cell(cell):
            return _result()

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        with caplog.at_level(logging.INFO, logger="harness_evaluator.orchestrator.engine"):
            await orch.run()

        messages = [r.getMessage() for r in caplog.records]
        assert any("\u25b6 [1/2] start h1 | m1 | t1 | rep 0" in m for m in messages)
        assert any("\u25b6 [2/2] start" in m for m in messages)
        # The outcome line must carry the figures that make a result
        # interpretable without opening the results DB.
        passes = [m for m in messages if "pass h1 | m1 | t1" in m]
        assert len(passes) == 2
        assert "$0.0200" in passes[0]
        assert "7 API calls" in passes[0]
        assert "150 tokens" in passes[0]
        assert "score 1.00" in passes[0]

    async def test_eval_failure_is_logged_with_class_and_reason(
        self, config, store, caplog
    ):
        async def run_cell(cell):
            return _result(
                exit_class="fail",
                success=0.0,
                error_class="no_change",
                error_message="No changes were made to the repository",
            )

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        with caplog.at_level(logging.INFO, logger="harness_evaluator.orchestrator.engine"):
            progress = await orch.run()

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "FAIL h1 | m1 | t1 | rep 0" in m and "[no_change]" in m for m in messages
        )
        assert any("No changes were made" in m for m in messages)
        # An eval failure is not an infrastructure error.
        assert progress.failed == 2
        assert progress.errored == 0
        assert progress.error_classes == {"no_change": 2}


class TestInfraVersusEvalFailure:
    async def test_non_retryable_exception_counts_as_errored(self, config, store):
        async def run_cell(cell):
            raise RuntimeError("docker run failed (exit 125)")

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        progress = await orch.run()

        assert progress.completed == 0
        assert progress.failed == 2
        assert progress.errored == 2
        assert progress.error_classes == {"non_retryable": 2}
        assert all("non_retryable" in e for e in progress.errors)

    async def test_retry_exhaustion_counts_as_errored(self, config, store):
        async def run_cell(cell):
            raise RetryableError("timed out")

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        orch.RETRY_BASE_DELAY = 0.0
        progress = await orch.run()

        assert progress.errored == 2
        assert progress.error_classes == {"retry_exhausted": 2}

    async def test_infra_error_flag_on_a_completed_cell_counts_as_errored(
        self, config, store, caplog
    ):
        """A cell can complete and still have measured nothing: the runner
        flags that via ``infra_error`` (no captured API calls). It must be
        counted with the kills, and the reason must reach both the log and
        ``progress.errors``."""

        async def run_cell(cell):
            return _result(
                exit_class="fail",
                success=0.0,
                num_api_calls=0,
                total_cost=0.0,
                error_class="no_change",
                error_message="No changes were made to the repository",
                infra_error="no API calls were captured for this cell",
            )

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        with caplog.at_level(logging.WARNING, logger="harness_evaluator.orchestrator.engine"):
            progress = await orch.run()

        assert progress.failed == 2
        assert progress.errored == 2
        assert all("no API calls were captured" in e for e in progress.errors)
        assert any(
            "no API calls were captured" in r.getMessage() for r in caplog.records
        )

    async def test_infra_error_cells_are_rerun_on_resume(self, config, store):
        """An infra-errored cell measured nothing, so it must not be recorded
        as completed: a resume after fixing the cause has to re-run it, and
        the reason has to be persisted, not only shown in the summary."""
        calls: list[str] = []

        async def broken(cell):
            calls.append(cell.cell_id)
            return _result(
                exit_class="fail",
                success=0.0,
                num_api_calls=0,
                error_class="no_change",
                error_message="No changes were made to the repository",
                infra_error="no API calls were captured for this cell",
            )

        await Orchestrator(config, store, run_cell_fn=broken).run()
        assert store.get_completed_cells(config.name) == set()
        rows = store.get_all_results(config.name)
        assert rows
        assert all("no API calls were captured" in r["error_message"] for r in rows)
        assert all(r["exit_class"] == "non_retryable_kill" for r in rows)

        async def fixed(cell):
            calls.append(cell.cell_id)
            return _result()

        progress = await Orchestrator(config, store, run_cell_fn=fixed).run()
        assert len(calls) == 4
        assert progress.completed == 2
        assert progress.errored == 0
        assert len(store.get_completed_cells(config.name)) == 2

    async def test_progress_errors_are_redacted(self, config, store):
        """They reach the CLI summary and the TUI footer verbatim."""

        async def run_cell(cell):
            raise RuntimeError(
                "setup failed: ANTHROPIC_API_KEY=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA"
            )

        progress = await Orchestrator(config, store, run_cell_fn=run_cell).run()
        assert progress.errors
        assert not any("sk-ant-api03" in e for e in progress.errors)

    async def test_a_passing_cell_is_never_errored(self, config, store):
        async def run_cell(cell):
            return _result(infra_error=None)

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        progress = await orch.run()
        assert progress.errored == 0
        assert progress.error_classes == {}


class TestHeartbeat:
    async def test_heartbeat_reports_in_flight_cells(self, config, store, caplog):
        """A cell routinely runs for minutes with nothing else logging; that
        must not be indistinguishable from a hang."""

        async def run_cell(cell):
            await asyncio.sleep(0.15)
            return _result()

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        orch.HEARTBEAT_INTERVAL = 0.05
        with caplog.at_level(logging.INFO, logger="harness_evaluator.orchestrator.engine"):
            await orch.run()

        beats = [r.getMessage() for r in caplog.records if "still running" in r.getMessage()]
        assert beats, "expected at least one heartbeat during a slow cell"
        assert "in flight" in beats[0]

    async def test_heartbeat_is_silent_when_nothing_is_running(
        self, config, store, caplog
    ):
        """It must not chatter between cells, or after the matrix is done."""

        async def run_cell(cell):
            return _result()

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        orch.HEARTBEAT_INTERVAL = 0.01
        with caplog.at_level(logging.INFO, logger="harness_evaluator.orchestrator.engine"):
            await orch.run()
            # Give a cancelled heartbeat every chance to fire again.
            await asyncio.sleep(0.05)

        assert not [r for r in caplog.records if "still running" in r.getMessage()]

    async def test_heartbeat_does_not_outlive_the_run(self, config, store):
        async def run_cell(cell):
            return _result()

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        orch.HEARTBEAT_INTERVAL = 0.01
        await orch.run()
        # No stray task left behind to warn about (or keep logging) later.
        pending = [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        assert pending == []

    async def test_heartbeat_caps_the_listed_cells(self, config, store, caplog):
        """Under a large parallel_runs the in-flight list would otherwise
        make every heartbeat line arbitrarily long."""
        config.repeats = 6
        config.parallel_runs = 6

        async def run_cell(cell):
            await asyncio.sleep(0.15)
            return _result()

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        orch.HEARTBEAT_INTERVAL = 0.05
        with caplog.at_level(logging.INFO, logger="harness_evaluator.orchestrator.engine"):
            await orch.run()

        beats = [r.getMessage() for r in caplog.records if "still running" in r.getMessage()]
        assert beats
        assert "(+3)" in beats[0]
        assert beats[0].count("__r") == orch.HEARTBEAT_MAX_CELLS


class TestNothingToRun:
    async def test_fully_completed_matrix_warns(self, config, store, caplog):
        async def run_cell(cell):
            return _result()

        await Orchestrator(config, store, run_cell_fn=run_cell).run()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="harness_evaluator.orchestrator.engine"):
            await Orchestrator(config, store, run_cell_fn=run_cell).run()
        assert any("nothing to run" in r.getMessage() for r in caplog.records)


class TestNarrationFaultsAreContained:
    async def test_a_failing_heartbeat_does_not_fail_the_run(self, config, store):
        async def run_cell(cell):
            await asyncio.sleep(0.1)
            return _result()

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        orch.HEARTBEAT_INTERVAL = 0.02

        async def boom() -> None:
            raise RuntimeError("heartbeat broke")

        orch._log_heartbeat = boom  # type: ignore[method-assign]
        progress = await orch.run()
        assert progress.completed == 2

    async def test_a_narration_fault_is_not_reported_as_a_persist_failure(
        self, config, store, caplog
    ):
        async def run_cell(cell):
            return _result(success="not-a-number")

        orch = Orchestrator(config, store, run_cell_fn=run_cell)
        with caplog.at_level(logging.WARNING, logger="harness_evaluator.orchestrator.engine"):
            await orch.run()
        messages = [r.getMessage() for r in caplog.records]
        assert not any("Failed to persist" in m for m in messages)
        assert any("Could not narrate" in m for m in messages)
        assert len(store.get_completed_cells(config.name)) == 2


class TestProgressSnapshotCarriesNewFields:
    async def test_snapshot_includes_errored_and_error_classes(self, config, store):
        seen = []

        async def run_cell(cell):
            raise RuntimeError("boom")

        orch = Orchestrator(
            config, store, run_cell_fn=run_cell, on_progress=seen.append
        )
        await orch.run()

        final = seen[-1]
        assert final.errored == 2
        assert final.error_classes == {"non_retryable": 2}
        # Snapshots must be independent copies, not aliases of live state.
        assert final.error_classes is not orch.progress.error_classes
