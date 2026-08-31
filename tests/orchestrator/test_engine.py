"""Tests for the orchestrator engine."""

from __future__ import annotations

import pytest
import yaml

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import HarnessSpec, ModelSpec, RunConfig
from harness_evaluator.orchestrator.engine import (
    Orchestrator,
    OrchestratorProgress,
    RetryableError,
)
from harness_evaluator.orchestrator.results_store import ResultsStore


@pytest.fixture
def tmp_task_dir(tmp_path):
    """Create a temporary task library with one task."""
    task_data = {
        "tasks": [
            {
                "id": "test-1",
                "name": "Test",
                "track": "swe",
                "task_prompt": "Fix bug",
                "test_command": "echo pass",
            }
        ]
    }
    (tmp_path / "task.yaml").write_text(yaml.dump(task_data))
    return tmp_path


@pytest.fixture
def sample_config(tmp_task_dir):
    return RunConfig(
        name="engine-test",
        harnesses=[HarnessSpec(name="h1", adapter="a")],
        models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
        tasks=["*"],
        task_library_path=str(tmp_task_dir),
        repeats=2,
    )


@pytest.fixture
def results_store(tmp_path):
    return ResultsStore(str(tmp_path / "test_results.db"))


class TestOrchestratorDryRun:
    async def test_dry_run_completes(self, sample_config, results_store):
        """Test that the orchestrator runs with the dry-run cell function."""
        orch = Orchestrator(sample_config, results_store)
        progress = await orch.run()

        assert progress.total_cells == 2  # 1 harness × 1 model × 1 task × 2 repeats
        assert progress.completed == 2
        assert progress.failed == 0
        assert progress.total_cost > 0

    async def test_results_saved(self, sample_config, results_store):
        """Test that results are saved to the store."""
        orch = Orchestrator(sample_config, results_store)
        await orch.run()

        results = results_store.get_all_results("engine-test")
        assert len(results) == 2
        assert all(r["exit_class"] == "pass" for r in results)

    async def test_resumability(self, sample_config, results_store):
        """Test that completed cells are skipped on re-run."""
        # First run
        orch1 = Orchestrator(sample_config, results_store)
        await orch1.run()

        # Second run should skip all cells
        orch2 = Orchestrator(sample_config, results_store)
        progress = await orch2.run()

        assert progress.skipped == 2
        assert progress.completed == 0

    async def test_budget_accounts_for_prior_spend_on_resume(
        self, tmp_task_dir, results_store
    ):
        """A resumed run must not re-spend the full budget.

        First run consumes the whole budget on one cell; the second run
        (same config, same store) should have no remaining budget and skip
        the pending cell rather than letting it spend the full cap again.
        """
        config = RunConfig(
            name="resume-budget",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=2,
            budget_usd=0.001,  # only enough for exactly one cell
        )

        async def costly_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": 0.001,
                "latency_ms": 100,
            }

        orch1 = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress1 = await orch1.run()
        assert progress1.completed == 1
        assert progress1.skipped == 1

        # Resume: the one completed cell is skipped as done; the remaining
        # pending cell must be skipped for budget (prior spend consumed it).
        orch2 = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress2 = await orch2.run()
        assert progress2.completed == 0
        # No further cells should run; total recorded cost stays within budget.
        total_cost = results_store.get_total_cost("resume-budget")
        assert total_cost <= config.budget_usd + 1e-9


class TestOrchestratorRetry:
    async def test_retryable_error_retries(self, sample_config, results_store):
        """Test that retryable errors trigger retries."""
        call_count = 0

        async def failing_cell(cell):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RetryableError("Transient failure")
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.001,
                "latency_ms": 100,
            }

        # Override retry delay for testing
        Orchestrator.RETRY_BASE_DELAY = 0.01

        orch = Orchestrator(sample_config, results_store, run_cell_fn=failing_cell)
        progress = await orch.run()

        assert progress.completed == 2  # Both cells eventually pass
        assert call_count == 4  # 2 retries for first cell + 1 for second + 1 for second

    async def test_non_retryable_error_fails(self, sample_config, results_store):
        """Test that non-retryable errors fail immediately."""

        async def crashing_cell(cell):
            raise RuntimeError("Harness crashed")

        orch = Orchestrator(sample_config, results_store, run_cell_fn=crashing_cell)
        progress = await orch.run()

        assert progress.failed == 2
        assert progress.completed == 0


class TestOrchestratorBudget:
    async def test_budget_cap_skips_cells(self, tmp_task_dir, results_store):
        """Test that budget cap stops new cells from running."""
        config = RunConfig(
            name="budget-test",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=5,
            budget_usd=0.001,  # Very low budget
        )

        async def costly_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": 0.001,  # Each cell costs exactly the budget
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress = await orch.run()

        # First cell runs, subsequent cells should be skipped
        assert progress.completed >= 1
        assert progress.skipped >= 1

    async def test_budget_lock_protects_save(
        self, tmp_task_dir, results_store
    ):
        """Test that parallel cells don't exceed the budget.

        With the budget lock protecting the save, only one cell's cost
        should be recorded before the next cell's pre-check sees it.
        """
        config = RunConfig(
            name="budget-lock-test",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=4,
            budget_usd=0.002,  # Only enough for 2 cells at 0.001 each
            parallel_runs=4,
        )

        async def costly_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": 0.001,
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress = await orch.run()

        # Total recorded cost should not exceed the budget by more than
        # one cell's cost (the pre-check / post-update pattern means at
        # most parallel_runs cells can pass the pre-check before any save,
        # but the save is under the lock so cost is always consistent).
        total_cost = results_store.get_total_cost("budget-lock-test")
        assert total_cost <= 0.002 + 0.001  # budget + at most one extra cell
        assert progress.completed >= 1
        assert progress.skipped >= 1


class TestProgressCallback:
    """Tests for the on_progress callback hook."""

    async def test_callback_fires_on_cell_completion(self, sample_config, results_store):
        """on_progress should fire at least once per cell completion."""
        snapshots: list[OrchestratorProgress] = []

        def on_progress(snap: OrchestratorProgress) -> None:
            snapshots.append(snap)

        orch = Orchestrator(
            sample_config, results_store, on_progress=on_progress
        )
        progress = await orch.run()

        # 2 cells, each fires: running-start + completed + running-decrement
        # plus the initial skipped-notification. At minimum, we expect one
        # notification per cell completion.
        assert len(snapshots) >= 2
        # The final snapshot should match the returned progress.
        final = snapshots[-1]
        assert final.completed == progress.completed
        assert final.total_cells == progress.total_cells
        assert final.failed == progress.failed

    async def test_callback_snapshots_are_independent(self, sample_config, results_store):
        """Each snapshot should be a copy, not a live reference."""
        snapshots: list[OrchestratorProgress] = []

        def on_progress(snap: OrchestratorProgress) -> None:
            snapshots.append(snap)

        orch = Orchestrator(
            sample_config, results_store, on_progress=on_progress
        )
        await orch.run()

        # Mutate the first snapshot; the last should be unaffected.
        first = snapshots[0]
        first_completed = first.completed
        first.completed = 999
        last = snapshots[-1]
        assert last.completed != 999
        # Restore for cleanliness (not strictly necessary)
        first.completed = first_completed

    async def test_callback_provides_current_cell(self, sample_config, results_store):
        """The snapshot should include current_cell when a cell is running."""
        snapshots: list[OrchestratorProgress] = []

        def on_progress(snap: OrchestratorProgress) -> None:
            snapshots.append(snap)

        orch = Orchestrator(
            sample_config, results_store, on_progress=on_progress
        )
        await orch.run()

        # At least one snapshot should have current_cell set (the one
        # fired when a cell transitions to running).
        running_snaps = [s for s in snapshots if s.current_cell is not None]
        assert len(running_snaps) >= 1
        # All current_cell values should be valid cell IDs
        for snap in running_snaps:
            assert "h1" in snap.current_cell
            assert "test-1" in snap.current_cell

    async def test_no_callback_is_noop(self, sample_config, results_store):
        """Omitting on_progress should not raise or change behavior."""
        orch = Orchestrator(sample_config, results_store)
        progress = await orch.run()
        assert progress.completed == 2

    async def test_callback_fires_on_budget_skip(
        self, tmp_task_dir, results_store
    ):
        """on_progress should fire when a cell is skipped for budget."""
        config = RunConfig(
            name="skip-callback",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=2,
            budget_usd=0.0001,  # not enough for any cell
        )
        snapshots: list[OrchestratorProgress] = []

        def on_progress(snap: OrchestratorProgress) -> None:
            snapshots.append(snap)

        async def costly_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": 0.001,
                "latency_ms": 100,
            }

        orch = Orchestrator(
            config, results_store, run_cell_fn=costly_cell, on_progress=on_progress
        )
        await orch.run()

        # At least one snapshot should show skipped > 0
        skip_snaps = [s for s in snapshots if s.skipped > 0]
        assert len(skip_snaps) >= 1
