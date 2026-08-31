"""Tests for multi-phase results store (phase_results table)."""

from __future__ import annotations

import pytest

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelRole,
    ModelSpec,
    PhaseSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.orchestrator.results_store import ResultsStore


@pytest.fixture
def store(tmp_path):
    return ResultsStore(str(tmp_path / "test_phase.db"))


@pytest.fixture
def multiphase_cell():
    return RunCell(
        run_name="mp-run",
        harness=HarnessSpec(name="claude-code", adapter="claude_code"),
        model=ModelSpec(
            name="sonnet",
            provider="anthropic",
            api_key_env="KEY",
            role=ModelRole.IMPLEMENTATION,
        ),
        task=TaskSpec(
            id="mp-task",
            name="MP Task",
            track=TaskTrack.MULTI_PHASE,
            task_prompt="Fix bug",
            phases=[
                PhaseSpec(name="implement", task_prompt="Fix bug"),
                PhaseSpec(
                    name="review",
                    model_role=ModelRole.REVIEW,
                    task_prompt="Review it",
                ),
            ],
        ),
        repeat=0,
        review_model=ModelSpec(
            name="opus",
            provider="anthropic",
            api_key_env="KEY",
            role=ModelRole.REVIEW,
        ),
    )


class TestPhaseResults:
    def test_save_and_retrieve_phases(self, store, multiphase_cell) -> None:
        phases = [
            {
                "name": "implement",
                "trace_id": f"{multiphase_cell.cell_id}__phase-implement",
                "model": "sonnet",
                "model_role": "implementation",
                "exit_code": 0,
                "duration_ms": 5000.0,
                "timed_out": False,
                "usage": TokenUsage(input_tokens=1000, output_tokens=500),
                "total_cost": 0.05,
                "num_api_calls": 3,
            },
            {
                "name": "review",
                "trace_id": f"{multiphase_cell.cell_id}__phase-review",
                "model": "opus",
                "model_role": "review",
                "exit_code": 0,
                "duration_ms": 3000.0,
                "timed_out": False,
                "usage": TokenUsage(input_tokens=2000, output_tokens=1000),
                "total_cost": 0.15,
                "num_api_calls": 5,
            },
        ]
        store.save_phase_results(multiphase_cell.cell_id, "mp-run", phases)

        retrieved = store.get_phase_results(multiphase_cell.cell_id)
        assert len(retrieved) == 2
        assert retrieved[0]["phase_name"] == "implement"
        assert retrieved[0]["model"] == "sonnet"
        assert retrieved[0]["input_tokens"] == 1000
        assert retrieved[0]["total_cost"] == 0.05
        assert retrieved[1]["phase_name"] == "review"
        assert retrieved[1]["model"] == "opus"
        assert retrieved[1]["total_cost"] == 0.15

    def test_resave_clears_prior(self, store, multiphase_cell) -> None:
        """Saving phase results twice should replace, not duplicate."""
        phases_v1 = [
            {
                "name": "implement",
                "trace_id": "t1",
                "model": "sonnet",
                "model_role": "implementation",
                "exit_code": 0,
                "duration_ms": 1000.0,
                "timed_out": False,
            }
        ]
        store.save_phase_results(multiphase_cell.cell_id, "mp-run", phases_v1)
        assert len(store.get_phase_results(multiphase_cell.cell_id)) == 1

        phases_v2 = [
            {
                "name": "implement",
                "trace_id": "t1",
                "model": "sonnet",
                "model_role": "implementation",
                "exit_code": 0,
                "duration_ms": 2000.0,
                "timed_out": False,
            },
            {
                "name": "review",
                "trace_id": "t2",
                "model": "opus",
                "model_role": "review",
                "exit_code": 0,
                "duration_ms": 3000.0,
                "timed_out": False,
            },
        ]
        store.save_phase_results(multiphase_cell.cell_id, "mp-run", phases_v2)
        retrieved = store.get_phase_results(multiphase_cell.cell_id)
        assert len(retrieved) == 2

    def test_no_phase_results_returns_empty(self, store, multiphase_cell) -> None:
        assert store.get_phase_results(multiphase_cell.cell_id) == []

    def test_phases_without_usage(self, store, multiphase_cell) -> None:
        """Phases without usage data should default to zero tokens."""
        phases = [
            {
                "name": "implement",
                "trace_id": "t1",
                "model": "sonnet",
                "model_role": "implementation",
                "exit_code": 0,
                "duration_ms": 1000.0,
                "timed_out": False,
            }
        ]
        store.save_phase_results(multiphase_cell.cell_id, "mp-run", phases)
        retrieved = store.get_phase_results(multiphase_cell.cell_id)
        assert retrieved[0]["input_tokens"] == 0
        assert retrieved[0]["output_tokens"] == 0

    def test_save_and_retrieve_phase_stdout_stderr(self, store, multiphase_cell) -> None:
        """Phase stdout/stderr are stored and retrieved."""
        phases = [
            {
                "name": "implement",
                "trace_id": f"{multiphase_cell.cell_id}__phase-implement",
                "model": "sonnet",
                "model_role": "implementation",
                "exit_code": 0,
                "duration_ms": 5000.0,
                "timed_out": False,
                "stdout": "Working on the fix...\nDone.",
                "stderr": "Warning: deprecated API",
            },
            {
                "name": "review",
                "trace_id": f"{multiphase_cell.cell_id}__phase-review",
                "model": "opus",
                "model_role": "review",
                "exit_code": 1,
                "duration_ms": 3000.0,
                "timed_out": False,
                "stdout": "",
                "stderr": "Review failed: issues found",
            },
        ]
        store.save_phase_results(multiphase_cell.cell_id, "mp-run", phases)
        retrieved = store.get_phase_results(multiphase_cell.cell_id)
        assert len(retrieved) == 2
        assert retrieved[0]["stdout"] == "Working on the fix...\nDone."
        assert retrieved[0]["stderr"] == "Warning: deprecated API"
        assert retrieved[1]["stdout"] == ""
        assert retrieved[1]["stderr"] == "Review failed: issues found"

    def test_phase_stdout_stderr_defaults_to_none(self, store, multiphase_cell) -> None:
        """Phases without stdout/stderr should store None."""
        phases = [
            {
                "name": "implement",
                "trace_id": "t1",
                "model": "sonnet",
                "model_role": "implementation",
                "exit_code": 0,
                "duration_ms": 1000.0,
                "timed_out": False,
            }
        ]
        store.save_phase_results(multiphase_cell.cell_id, "mp-run", phases)
        retrieved = store.get_phase_results(multiphase_cell.cell_id)
        assert retrieved[0]["stdout"] is None
        assert retrieved[0]["stderr"] is None
