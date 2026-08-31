"""Tests for the results store."""

from __future__ import annotations

import pytest

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.orchestrator.results_store import ResultsStore


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

    def test_save_and_retrieve_harness_output(self, store, sample_cell):
        store.save_result(
            cell=sample_cell,
            exit_class="fail",
            success=0.0,
            error_class="no_change",
            error_message="No changes were made to the repository",
            harness_stdout="Building project...\nDone.",
            harness_stderr="Error: API key invalid",
        )
        result = store.get_result(sample_cell.cell_id)
        assert result is not None
        assert result["harness_stdout"] == "Building project...\nDone."
        assert result["harness_stderr"] == "Error: API key invalid"

    def test_harness_output_defaults_to_none(self, store, sample_cell):
        store.save_result(
            cell=sample_cell,
            exit_class="pass",
            success=1.0,
        )
        result = store.get_result(sample_cell.cell_id)
        assert result is not None
        assert result["harness_stdout"] is None
        assert result["harness_stderr"] is None

    def test_migration_adds_harness_output_columns(self, tmp_path):
        """Existing DBs without harness_stdout/stderr get them via ALTER TABLE."""
        import sqlite3

        db_path = str(tmp_path / "old_db.db")
        # Create an old-style schema without the new columns.
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE run_results (
                    cell_id TEXT PRIMARY KEY,
                    run_name TEXT NOT NULL,
                    harness TEXT NOT NULL,
                    model TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    track TEXT NOT NULL,
                    repeat INTEGER NOT NULL,
                    exit_class TEXT NOT NULL,
                    success REAL NOT NULL,
                    error_class TEXT,
                    error_message TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    total_cost REAL DEFAULT 0.0,
                    latency_ms REAL DEFAULT 0.0,
                    time_to_first_attempt_ms REAL DEFAULT 0.0,
                    num_api_calls INTEGER DEFAULT 0,
                    num_tool_calls INTEGER DEFAULT 0,
                    diff TEXT,
                    test_output TEXT,
                    harness_metadata TEXT,
                    timestamp TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0
                );
                CREATE TABLE phase_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cell_id TEXT NOT NULL,
                    run_name TEXT NOT NULL,
                    phase_name TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    model_role TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    duration_ms REAL DEFAULT 0.0,
                    timed_out INTEGER DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_cost REAL DEFAULT 0.0,
                    num_api_calls INTEGER DEFAULT 0,
                    error TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (cell_id) REFERENCES run_results(cell_id)
                );
                """
            )
            conn.commit()

        # Opening with ResultsStore should migrate the schema.
        store = ResultsStore(db_path)
        # Verify the new columns exist.
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(run_results)")}
            assert "harness_stdout" in cols
            assert "harness_stderr" in cols
            phase_cols = {row[1] for row in conn.execute("PRAGMA table_info(phase_results)")}
            assert "stdout" in phase_cols
            assert "stderr" in phase_cols

        # Verify we can save and retrieve with the new columns.
        store.save_result(
            cell=RunCell(
                run_name="migrated-run",
                harness=HarnessSpec(name="opencode", adapter="opencode"),
                model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
                task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="x"),
                repeat=0,
            ),
            exit_class="fail",
            success=0.0,
            harness_stdout="hello",
            harness_stderr="world",
        )
