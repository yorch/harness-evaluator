"""Tests for the orchestrator engine."""

from __future__ import annotations

import logging

import pytest
import yaml

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import (
    CostMode,
    HarnessSpec,
    ModelSpec,
    RunCell,
    RunConfig,
    TaskSpec,
    TaskTrack,
)
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

    async def test_partial_headroom_denies_admission_even_when_remaining_is_positive(
        self, tmp_task_dir, results_store
    ):
        """The admission gate is `remaining <= 0 OR remaining < estimate` --
        both halves matter. A cell whose estimate exceeds the (still
        positive) remaining budget must be denied, not just a cell that
        would push remaining below zero outright.

        Without the `remaining < estimate` half, this scenario (budget
        $1.00, 5 cells at a $0.20 per-cell estimate but $0.85 real cost)
        admits a 2nd cell once the 1st cell's reconciliation still leaves
        $0.15 > $0.00 remaining, overspending the cap by 70% -- billable
        $1.70 against a $1.00 cap instead of stopping at $0.85.
        """
        config = RunConfig(
            name="partial-headroom-test",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=5,
            budget_usd=1.00,
            parallel_runs=1,
        )

        async def costly_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": 0.85,
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress = await orch.run()

        assert progress.completed == 1
        assert progress.skipped == 4
        assert results_store.get_billable_cost("partial-headroom-test") == pytest.approx(0.85)

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


class TestSubscriptionCostMode:
    """Tests for F5/F8/F9: subscription cells must not eat the dollar budget,
    while progress.total_cost (and get_total_cost) still track their true cost."""

    async def test_subscription_cost_does_not_reduce_budget(
        self, tmp_task_dir, results_store
    ):
        """A subscription cell reporting non-zero cost must not deduct from
        the remaining budget, and must not cause later cells to be skipped
        for budget reasons.

        Without the F5 fix, _reconcile_reservation deducts the full actual
        cost from _remaining_budget even for a subscription cell, driving
        the budget negative and causing the next cells to be skipped.
        """
        config = RunConfig(
            name="subscription-budget",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[
                ModelSpec(
                    name="m1",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.SUBSCRIPTION,
                )
            ],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=3,
            budget_usd=0.0005,  # would only cover half of one cell's reported cost
        )

        async def subscription_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": 0.001,  # non-zero, reported for informational tracking
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=subscription_cell)
        progress = await orch.run()

        assert progress.completed == 3
        assert progress.skipped == 0
        # progress.total_cost is the informational figure and still
        # accumulates the true cost of every subscription cell.
        assert progress.total_cost == pytest.approx(0.003)

    async def test_mixed_run_charges_only_platform_cells_against_budget(
        self, tmp_task_dir, results_store
    ):
        """A mixed platform + subscription run must charge only the platform
        cell against the dollar budget cap, while progress.total_cost (and
        get_total_cost) reflect the true cost of both.

        Without the F5 fix, the subscription cell's actual cost wrongly
        drains the budget reserved for the platform cell that follows it,
        causing it to be skipped.
        """
        config = RunConfig(
            name="mixed-budget",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[
                ModelSpec(
                    name="m-sub",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.SUBSCRIPTION,
                ),
                ModelSpec(
                    name="m-platform",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.PLATFORM,
                ),
            ],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=0.001,
        )

        async def mixed_cell(cell):
            cost = 0.0009 if cell.model.cost_mode == CostMode.SUBSCRIPTION else 0.0005
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=100, output_tokens=50),
                "total_cost": cost,
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=mixed_cell)
        progress = await orch.run()

        assert progress.completed == 2
        assert progress.skipped == 0
        assert progress.total_cost == pytest.approx(0.0009 + 0.0005)
        # Only the platform cell's cost is billable against the budget.
        assert results_store.get_billable_cost("mixed-budget") == pytest.approx(0.0005)
        assert results_store.get_total_cost("mixed-budget") == pytest.approx(0.0009 + 0.0005)

    async def test_resume_without_budget_reports_prior_spend_in_total_cost(
        self, tmp_task_dir, results_store
    ):
        """F8: resuming a run with no budget_usd must still seed
        progress.total_cost from prior spend, not leave it at 0.0."""
        config = RunConfig(
            name="resume-no-budget",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            # budget_usd intentionally left as None
        )

        async def costed_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.0037,
                "latency_ms": 100,
            }

        orch1 = Orchestrator(config, results_store, run_cell_fn=costed_cell)
        progress1 = await orch1.run()
        assert progress1.completed == 1
        assert progress1.total_cost == pytest.approx(0.0037)

        # Resume: the cell is skipped as already completed, but total_cost
        # must still reflect prior spend even though no budget is set.
        orch2 = Orchestrator(config, results_store, run_cell_fn=costed_cell)
        progress2 = await orch2.run()
        assert progress2.completed == 0
        assert progress2.skipped == 1
        assert progress2.total_cost == pytest.approx(0.0037)

    async def test_resume_with_budget_uses_billable_cost_baseline(
        self, tmp_task_dir, results_store
    ):
        """F5: the resume baseline must use get_billable_cost, not
        get_total_cost, so prior subscription spend does not eat the budget
        available to pending platform cells.

        Without the fix, the resumed run's baseline includes the completed
        subscription cell's cost, driving the remaining budget to 0 and
        causing the pending platform cell to be skipped.
        """
        sub_model = ModelSpec(
            name="m-sub",
            provider="anthropic",
            api_key_env="X",
            cost_mode=CostMode.SUBSCRIPTION,
        )
        platform_model = ModelSpec(
            name="m-platform",
            provider="anthropic",
            api_key_env="X",
            cost_mode=CostMode.PLATFORM,
        )
        harness = HarnessSpec(name="h1", adapter="a")

        # Phase 1: only the subscription model, no budget cap, so it
        # completes and records a non-zero cost under this run name.
        config1 = RunConfig(
            name="resume-mixed-budget",
            harnesses=[harness],
            models=[sub_model],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )

        async def sub_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.002,
                "latency_ms": 100,
            }

        orch1 = Orchestrator(config1, results_store, run_cell_fn=sub_cell)
        progress1 = await orch1.run()
        assert progress1.completed == 1

        # Phase 2: resume under the same run name, now with a platform model
        # added and a tight budget. The subscription cell is already
        # completed (skipped by resumability); the pending platform cell
        # must still get to run because the baseline excludes the prior
        # subscription spend.
        config2 = RunConfig(
            name="resume-mixed-budget",
            harnesses=[harness],
            models=[sub_model, platform_model],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=0.001,
        )

        async def mixed_cell(cell):
            cost = 0.002 if cell.model.cost_mode == CostMode.SUBSCRIPTION else 0.0004
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": cost,
                "latency_ms": 100,
            }

        orch2 = Orchestrator(config2, results_store, run_cell_fn=mixed_cell)
        progress2 = await orch2.run()

        assert progress2.completed == 1  # the platform cell ran
        assert progress2.skipped == 1  # the subscription cell, via resumability


class TestBudgetGateZeroEstimateGuard:
    """Important A: a zero-dollar (budget-exempt) cell must never be denied
    for "budget" just because an earlier platform cell's overspend drove
    `_remaining_budget` negative."""

    async def test_subscription_cell_runs_after_platform_overspend(
        self, tmp_task_dir, results_store
    ):
        """Platform cell overspends its reservation (real cost > reserved
        share), driving the naive `_remaining_budget` negative; the
        subscription cell that follows must still run.
        """
        config = RunConfig(
            name="overspend-then-subscription",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[
                ModelSpec(
                    name="m-platform",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.PLATFORM,
                ),
                ModelSpec(
                    name="m-sub",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.SUBSCRIPTION,
                ),
            ],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=0.001,  # reserves $0.0005 per cell (2 cells total)
        )

        async def cell_fn(cell):
            cost = 0.002 if cell.model.cost_mode == CostMode.PLATFORM else 0.0009
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": cost,
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=cell_fn)
        progress = await orch.run()

        assert progress.completed == 2
        assert progress.skipped == 0
        assert not any(
            "Budget cap reached" in reason for reason in progress.skip_reasons.values()
        )


class TestReviewModelAndJudgeBudgetLeak:
    """Important B: a cell is budget-exempt only if EVERY model that can
    incur spend under its trace is subscription-covered, and only if the
    task does not invoke the LLM judge."""

    async def test_platform_review_model_spend_is_billed(self, tmp_task_dir, results_store):
        """A subscription implementation model paired with a platform
        review model must NOT be exempt: the review phase's real spend is
        folded into the same trace's total_cost.
        """
        harness = HarnessSpec(name="h1", adapter="a")
        sub_model = ModelSpec(
            name="m-sub", provider="anthropic", api_key_env="X", cost_mode=CostMode.SUBSCRIPTION
        )
        platform_review = ModelSpec(
            name="m-review-plat",
            provider="anthropic",
            api_key_env="X",
            cost_mode=CostMode.PLATFORM,
        )
        task = TaskSpec(id="test-1", name="Test", track=TaskTrack.SWE, task_prompt="Fix bug")
        cell = RunCell(
            run_name="review-leak",
            harness=harness,
            model=sub_model,
            task=task,
            repeat=0,
            review_model=platform_review,
        )
        config = RunConfig(
            name="review-leak",
            harnesses=[harness],
            models=[sub_model, platform_review],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=10.0,
        )

        async def review_cell(c):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 1.10,  # $1.00 impl (subscription) + $0.10 review (platform)
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=review_cell)
        orch._total_cells = 1
        await orch._run_cell_with_budget_check(cell)

        assert orch.progress.completed == 1
        assert results_store.get_billable_cost("review-leak") == pytest.approx(1.10)
        assert orch._remaining_budget == pytest.approx(10.0 - 1.10)
        row = results_store.get_result(cell.cell_id, "review-leak")
        assert row is not None
        assert row["cost_mode"] == "platform"

    async def test_open_ended_judge_spend_is_billed(self, tmp_task_dir, results_store):
        """A subscription model on the open_ended track must NOT be exempt
        either: the LLM judge's calls land in the same trace and are real,
        platform-billed API spend with no subscription coverage of their
        own.
        """
        harness = HarnessSpec(name="h1", adapter="a")
        sub_model = ModelSpec(
            name="m-sub", provider="anthropic", api_key_env="X", cost_mode=CostMode.SUBSCRIPTION
        )
        task = TaskSpec(
            id="oe-1", name="Open-ended", track=TaskTrack.OPEN_ENDED, task_prompt="Write X"
        )
        cell = RunCell(
            run_name="judge-leak", harness=harness, model=sub_model, task=task, repeat=0
        )
        config = RunConfig(
            name="judge-leak",
            harnesses=[harness],
            models=[sub_model],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=1.0,
        )

        async def judge_cell(c):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.30,  # judge spend: real API dollars
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=judge_cell)
        orch._total_cells = 1
        await orch._run_cell_with_budget_check(cell)

        assert results_store.get_billable_cost("judge-leak") == pytest.approx(0.30)
        assert orch._remaining_budget == pytest.approx(1.0 - 0.30)
        row = results_store.get_result(cell.cell_id, "judge-leak")
        assert row is not None
        assert row["cost_mode"] == "platform"

    async def test_subscription_review_model_stays_exempt(self, tmp_task_dir, results_store):
        """When BOTH the implementation and review models are
        subscription-mode, the cell remains fully budget-exempt — guards
        against an overcorrection that requires review_model to always be
        None.
        """
        harness = HarnessSpec(name="h1", adapter="a")
        sub_model = ModelSpec(
            name="m-sub", provider="anthropic", api_key_env="X", cost_mode=CostMode.SUBSCRIPTION
        )
        sub_review = ModelSpec(
            name="m-review-sub",
            provider="anthropic",
            api_key_env="X",
            cost_mode=CostMode.SUBSCRIPTION,
        )
        task = TaskSpec(id="test-1", name="Test", track=TaskTrack.SWE, task_prompt="Fix bug")
        cell = RunCell(
            run_name="both-sub",
            harness=harness,
            model=sub_model,
            task=task,
            repeat=0,
            review_model=sub_review,
        )
        config = RunConfig(
            name="both-sub",
            harnesses=[harness],
            models=[sub_model, sub_review],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=1.0,
        )

        async def cheap_cell(c):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 5.0,  # reported, but fully subscription-covered
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=cheap_cell)
        orch._total_cells = 1
        await orch._run_cell_with_budget_check(cell)

        assert results_store.get_billable_cost("both-sub") == pytest.approx(0.0)
        assert orch._remaining_budget == pytest.approx(1.0)
        row = results_store.get_result(cell.cell_id, "both-sub")
        assert row is not None
        assert row["cost_mode"] == "subscription"


class TestOverBudgetWarningUsesBillableCost:
    """Important C: leak site 3 (the post-cell over-budget warning) has to
    compare BILLABLE cost against the cap, not total cost."""

    async def test_no_warning_for_subscription_only_overspend(
        self, tmp_task_dir, results_store, caplog
    ):
        """A subscription cell reporting cost far beyond the cap must not
        log "Budget exceeded", since none of that cost is billable.
        """
        config = RunConfig(
            name="warn-billable",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[
                ModelSpec(
                    name="m-sub",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.SUBSCRIPTION,
                )
            ],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=0.0005,
        )

        async def costly_subscription_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 10.0,  # would blow the cap many times over if billable
                "latency_ms": 100,
            }

        with caplog.at_level(logging.WARNING, logger="harness_evaluator.orchestrator.engine"):
            orch = Orchestrator(config, results_store, run_cell_fn=costly_subscription_cell)
            progress = await orch.run()

        assert progress.completed == 1
        assert "Budget exceeded" not in caplog.text


class TestProgressTotalCostDoesNotDoubleCount:
    """Minor: progress.total_cost must never drift from what is actually
    persisted, even when set_cell_state('completed') fails transiently
    after save_result has already committed."""

    async def test_total_cost_matches_store_after_transient_set_cell_state_failure(
        self, tmp_task_dir, results_store
    ):
        config = RunConfig(
            name="flaky-state",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )

        async def costed_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.01,
                "latency_ms": 100,
            }

        original_set_cell_state = results_store.set_cell_state
        calls = {"n": 0}

        def flaky_set_cell_state(cell_id, run_name, status, *args, **kwargs):
            if status == "completed" and calls["n"] == 0:
                calls["n"] += 1
                raise RuntimeError("simulated transient failure")
            return original_set_cell_state(cell_id, run_name, status, *args, **kwargs)

        results_store.set_cell_state = flaky_set_cell_state  # type: ignore[method-assign]

        orch1 = Orchestrator(config, results_store, run_cell_fn=costed_cell)
        progress1 = await orch1.run()
        # The row was saved but state never flipped to "completed", so the
        # cell is not resumability-skipped and progress never incremented
        # total_cost on this (failed-to-persist-state) path.
        assert progress1.completed == 0
        assert results_store.get_completed_cells("flaky-state") == set()

        results_store.set_cell_state = original_set_cell_state

        orch2 = Orchestrator(config, results_store, run_cell_fn=costed_cell)
        progress2 = await orch2.run()

        assert progress2.completed == 1
        assert progress2.total_cost == pytest.approx(0.01)
        assert progress2.total_cost == pytest.approx(
            results_store.get_total_cost("flaky-state")
        )



class TestOverspendDebtNotErasedByRefund:
    """Important A (round 2): _reconcile_reservation must NOT floor
    _remaining_budget at 0.0. Under parallel_runs > 1, several cells can
    hold outstanding reservations at once; flooring would let one platform
    cell's overspend debt be silently erased by a later, unrelated
    sibling's refund, re-opening a budget cap that was already blown."""

    async def test_sibling_refund_does_not_erase_overspend_debt(
        self, tmp_task_dir, results_store
    ):
        harness = HarnessSpec(name="h1", adapter="a")
        platform_model = ModelSpec(
            name="m-platform",
            provider="anthropic",
            api_key_env="X",
            cost_mode=CostMode.PLATFORM,
        )
        task = TaskSpec(id="test-1", name="Test", track=TaskTrack.SWE, task_prompt="Fix bug")
        config = RunConfig(
            name="overspend-debt",
            harnesses=[harness],
            models=[platform_model],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=1.20,
        )

        def cell(repeat):
            return RunCell(
                run_name="overspend-debt",
                harness=harness,
                model=platform_model,
                task=task,
                repeat=repeat,
            )

        cell_a, cell_b1, cell_b2, cell_f = cell(0), cell(1), cell(2), cell(3)

        orch = Orchestrator(config, results_store)
        orch._total_cells = 6  # $0.20 reserved per cell, matching the repro
        orch._remaining_budget = 1.20

        # Simulate three cells with outstanding reservations under
        # parallel_runs > 1 (each holding its $0.20 share).
        for c in (cell_a, cell_b1, cell_b2):
            orch._remaining_budget -= 0.20
            orch._reservations[c.cell_id] = 0.20

        orch._reconcile_reservation(cell_a, 2.00)  # A overspends badly
        orch._reconcile_reservation(cell_b1, 0.0)  # B1 refunds in full
        orch._reconcile_reservation(cell_b2, 0.0)  # B2 refunds in full

        # Netting A's overspend against both full refunds still leaves
        # real debt — a floor at 0.0 would erase it instead.
        assert orch._remaining_budget == pytest.approx(-0.80)

        # A later platform cell must still be correctly denied for budget.
        async def cell_fn(c):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.50,
                "latency_ms": 100,
            }

        orch.run_cell_fn = cell_fn
        await orch._run_cell_with_budget_check(cell_f)

        assert orch.progress.completed == 0
        assert orch.progress.skipped == 1
        assert cell_f.cell_id in orch.progress.skip_reasons
        assert "Budget cap reached" in orch.progress.skip_reasons[cell_f.cell_id]


class TestZeroBudgetIsNotInert:
    """Minor: budget_usd=0.0 is a valid config (RunConfig.budget_usd has no
    gt/ge constraint) and must deny EVERY non-exempt cell outright — not
    admit one before denying the rest, and not leak parallel_runs cells
    before the first reconcile — while exempt cells still run regardless."""

    async def test_budget_usd_zero_skips_non_exempt_but_runs_exempt(
        self, tmp_task_dir, results_store
    ):
        config = RunConfig(
            name="zero-budget",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[
                ModelSpec(
                    name="m-platform1",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.PLATFORM,
                ),
                ModelSpec(
                    name="m-platform2",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.PLATFORM,
                ),
                ModelSpec(
                    name="m-sub",
                    provider="anthropic",
                    api_key_env="X",
                    cost_mode=CostMode.SUBSCRIPTION,
                ),
            ],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=0.0,
        )

        async def cell_fn(cell):
            cost = 1.0 if cell.model.cost_mode == CostMode.PLATFORM else 0.0
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": cost,
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=cell_fn)
        progress = await orch.run()

        # With no budget at all (remaining starts, and stays, <= 0), EVERY
        # non-exempt cell is denied outright — including the very first
        # one, unlike the pre-round-3 gate. The subscription (exempt) cell
        # still runs regardless.
        assert progress.completed == 1
        assert progress.skipped == 2
        budget_skips = {
            cid
            for cid, reason in progress.skip_reasons.items()
            if "Budget cap reached" in reason
        }
        assert budget_skips == {
            "h1__m-platform1__test-1__r0",
            "h1__m-platform2__test-1__r0",
        }
        assert results_store.get_billable_cost("zero-budget") == pytest.approx(0.0)

    async def test_budget_usd_zero_denies_all_non_exempt_under_parallel_runs(
        self, tmp_task_dir, results_store
    ):
        """Round-3 finding 2: with a zero estimate, `_remaining_budget -=
        estimate` reserves nothing, so under parallel_runs > 1 every
        in-flight non-exempt cell used to be admitted before any of them
        reconciled (the pre-fix repro: 8 cells / $8.00 billed at
        parallel_runs=8). The `remaining <= 0` half of the gate denies
        every non-exempt cell outright, independent of concurrency.
        """
        platform_models = [
            ModelSpec(
                name=f"m-platform{i}",
                provider="anthropic",
                api_key_env="X",
                cost_mode=CostMode.PLATFORM,
            )
            for i in range(8)
        ]
        sub_model = ModelSpec(
            name="m-sub", provider="anthropic", api_key_env="X", cost_mode=CostMode.SUBSCRIPTION
        )
        config = RunConfig(
            name="zero-budget-parallel",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[*platform_models, sub_model],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=0.0,
            parallel_runs=8,
        )

        async def cell_fn(cell):
            cost = 1.0 if cell.model.cost_mode == CostMode.PLATFORM else 0.0
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": cost,
                "latency_ms": 100,
            }

        orch = Orchestrator(config, results_store, run_cell_fn=cell_fn)
        progress = await orch.run()

        assert progress.completed == 1  # only the subscription cell
        assert progress.skipped == 8  # all 8 platform cells denied
        assert results_store.get_billable_cost("zero-budget-parallel") == pytest.approx(0.0)



class TestTotalCostRecomputeIsRobustToStoreFailure:
    """Minor (round 2): if the post-completion get_total_cost() re-derive
    raises (e.g. "database is locked" under parallel_runs > 1), the cell's
    result and "completed" state are already durably persisted by that
    point, so progress.completed must still be incremented rather than
    silently under-reporting a cell that actually finished."""

    async def test_completed_counter_increments_despite_get_total_cost_failure(
        self, tmp_task_dir, results_store
    ):
        config = RunConfig(
            name="flaky-total-cost",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )

        async def costed_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.02,
                "latency_ms": 100,
            }

        original_get_total_cost = results_store.get_total_cost
        calls = {"n": 0}

        def flaky_get_total_cost(run_name):
            calls["n"] += 1
            # Let the F8 seed call (the 1st, at the top of run()) succeed;
            # fail only the post-completion re-derive (the 2nd).
            if calls["n"] == 2:
                raise RuntimeError("simulated transient store failure")
            return original_get_total_cost(run_name)

        results_store.get_total_cost = flaky_get_total_cost  # type: ignore[method-assign]

        orch = Orchestrator(config, results_store, run_cell_fn=costed_cell)
        progress = await orch.run()

        # The cell's result and "completed" state were fully persisted
        # BEFORE the flaky re-derive call; the counter must still reflect
        # that even though the store read failed.
        assert progress.completed == 1
        assert results_store.get_completed_cells("flaky-total-cost") == {
            "h1__m1__test-1__r0"
        }
        # total_cost falls back to the incremental value when the
        # store re-derive fails.
        assert progress.total_cost == pytest.approx(0.02)



class TestResumeBaselineDebtSurvivesRestart:
    """Round-3 finding 1: the resume baseline
    (``already_spent = get_billable_cost(...)``; ``remaining = budget -
    already_spent``) must NOT floor at 0.0 — overspend debt has to survive
    a restart exactly as it survives within a run (see
    _reconcile_reservation), or it re-opens a cap that was already blown
    on every resume."""

    async def test_overspend_debt_survives_resume_and_blocks_further_cells(
        self, tmp_task_dir, results_store
    ):
        harness = HarnessSpec(name="h1", adapter="a")
        platform_model = ModelSpec(
            name="m-platform",
            provider="anthropic",
            api_key_env="X",
            cost_mode=CostMode.PLATFORM,
        )
        config = RunConfig(
            name="resume-debt",
            harnesses=[harness],
            models=[platform_model],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=6,
            budget_usd=1.20,
        )

        async def costly_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 2.00,
                "latency_ms": 100,
            }

        orch1 = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress1 = await orch1.run()
        assert progress1.completed == 1
        assert progress1.skipped == 5
        assert results_store.get_billable_cost("resume-debt") == pytest.approx(2.00)

        # Resume: the debt from the first cell's overspend (-$0.80) must
        # survive the restart, not reset to $0.00 — so no further cell is
        # admitted and no further money is spent.
        orch2 = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress2 = await orch2.run()
        assert progress2.completed == 0
        assert orch2._remaining_budget == pytest.approx(-0.80)
        assert results_store.get_billable_cost("resume-debt") == pytest.approx(2.00)

        # A second resume must show the same thing — debt does not reset
        # or partially heal across repeated restarts.
        orch3 = Orchestrator(config, results_store, run_cell_fn=costly_cell)
        progress3 = await orch3.run()
        assert progress3.completed == 0
        assert orch3._remaining_budget == pytest.approx(-0.80)
        assert results_store.get_billable_cost("resume-debt") == pytest.approx(2.00)


class TestBillableCostWarningIsRobustToStoreFailure:
    """Round-3 finding 3: the get_billable_cost() read for the over-budget
    warning has the identical un-hardened-read defect that
    TestTotalCostRecomputeIsRobustToStoreFailure covers for get_total_cost
    one line later. Only budgeted runs execute this read, which is
    precisely the parallel_runs > 1 / "database is locked" scenario the
    finding cited — a failure here must not prevent progress.completed
    (or the total_cost re-derive) from updating for an already-persisted
    cell."""

    async def test_completed_counter_increments_despite_get_billable_cost_failure(
        self, tmp_task_dir, results_store
    ):
        config = RunConfig(
            name="flaky-billable-cost",
            harnesses=[HarnessSpec(name="h1", adapter="a")],
            models=[ModelSpec(name="m1", provider="anthropic", api_key_env="X")],
            tasks=["*"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
            budget_usd=10.0,  # only budgeted runs execute the get_billable_cost read
        )

        async def costed_cell(cell):
            return {
                "exit_class": "pass",
                "success": 1.0,
                "usage": TokenUsage(input_tokens=10, output_tokens=5),
                "total_cost": 0.02,
                "latency_ms": 100,
            }

        original_get_billable_cost = results_store.get_billable_cost
        calls = {"n": 0}

        def flaky_get_billable_cost(run_name):
            calls["n"] += 1
            # Let the resume-baseline call (the 1st, at the top of run())
            # succeed; fail only the post-completion over-budget-warning
            # read (the 2nd).
            if calls["n"] == 2:
                raise RuntimeError("simulated database is locked")
            return original_get_billable_cost(run_name)

        results_store.get_billable_cost = flaky_get_billable_cost  # type: ignore[method-assign]

        orch = Orchestrator(config, results_store, run_cell_fn=costed_cell)
        progress = await orch.run()

        # The cell's result and "completed" state were fully persisted
        # BEFORE the flaky billable-cost read; the counters must still
        # reflect that even though the warning check's store read failed.
        assert progress.completed == 1
        assert results_store.get_completed_cells("flaky-billable-cost") == {
            "h1__m1__test-1__r0"
        }
        assert progress.total_cost == pytest.approx(0.02)
