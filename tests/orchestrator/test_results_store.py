"""Tests for the results store."""

from __future__ import annotations

import pytest

from harnessbench.gateway.models import TokenUsage
from harnessbench.orchestrator.config import HarnessSpec, ModelSpec, RunCell, TaskSpec, TaskTrack
from harnessbench.orchestrator.results_store import ResultsStore


@pytest.fixture
def store(tmp_path):
    return ResultsStore(str(tmp_path / "test_results.db"))


@pytest.fixture
def sample_cell():
    return RunCell(
        run_name="test-run",
        harness=HarnessSpec(name="opencode", adapter="opencode"),
        model=ModelSpec(name="claude-sonnet-4-20250514", provider="anthropic", api_key_env="X"),
        task=TaskSpec(
            id="task-1",
            name="Task 1",
            track=TaskTrack.SWE,
            task_prompt="Fix bug",
        ),
        repeat=0,
    )


class TestResultsStore:
    def test_save_and_retrieve(self, store, sample_cell):
        store.save_result(
            cell=sample_cell,
            exit_class="pass",
            success=1.0,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            total_cost=0.001,
            latency_ms=5000,
            num_api_calls=3,
        )

        result = store.get_result(sample_cell.cell_id)
        assert result is not None
        assert result["exit_class"] == "pass"
        assert result["success"] == 1.0
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["total_cost"] == pytest.approx(0.001)
        assert result["latency_ms"] == pytest.approx(5000)
        assert result["num_api_calls"] == 3

    def test_get_all_results(self, store, sample_cell):
        for i in range(3):
            cell = sample_cell.model_copy(update={"repeat": i})
            store.save_result(
                cell=cell,
                exit_class="pass",
                success=1.0,
            )

        results = store.get_all_results("test-run")
        assert len(results) == 3
        assert all(r["run_name"] == "test-run" for r in results)

    def test_cell_state_tracking(self, store, sample_cell):
        store.set_cell_state(sample_cell.cell_id, "test-run", "running")
        assert store.get_cell_state(sample_cell.cell_id) == "running"

        store.set_cell_state(sample_cell.cell_id, "test-run", "completed")
        assert store.get_cell_state(sample_cell.cell_id) == "completed"

    def test_completed_cells_for_resumability(self, store, sample_cell):
        # Mark some cells as completed
        for i in range(3):
            cell = sample_cell.model_copy(update={"repeat": i})
            store.set_cell_state(cell.cell_id, "test-run", "completed")

        completed = store.get_completed_cells("test-run")
        assert len(completed) == 3

    def test_total_cost(self, store, sample_cell):
        for i in range(3):
            cell = sample_cell.model_copy(update={"repeat": i})
            store.save_result(
                cell=cell,
                exit_class="pass",
                success=1.0,
                total_cost=0.01 * (i + 1),
            )

        total = store.get_total_cost("test-run")
        assert total == pytest.approx(0.06)  # 0.01 + 0.02 + 0.03
