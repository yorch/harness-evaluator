"""Tests for per-cell phase state in ResultsStore."""

from __future__ import annotations

from harness_evaluator.orchestrator.results_store import ResultsStore


class TestPhaseState:
    def test_set_and_get_cell_phase(self, tmp_path) -> None:
        store = ResultsStore(tmp_path / "test.db")
        store.set_cell_state("cell1", "run1", "running")
        store.set_cell_phase("cell1", "run1", "harness_running")
        phases = store.get_running_cell_phases("run1")
        assert "cell1" in phases
        phase, started_at = phases["cell1"]
        assert phase == "harness_running"
        assert started_at is not None

    def test_get_running_cell_phases_empty(self, tmp_path) -> None:
        store = ResultsStore(tmp_path / "test.db")
        phases = store.get_running_cell_phases("run1")
        assert phases == {}

    def test_only_running_cells_returned(self, tmp_path) -> None:
        store = ResultsStore(tmp_path / "test.db")
        store.set_cell_state("cell1", "run1", "running")
        store.set_cell_phase("cell1", "run1", "harness_running")
        store.set_cell_state("cell2", "run1", "completed")
        store.set_cell_phase("cell2", "run1", "evaluating")
        phases = store.get_running_cell_phases("run1")
        assert "cell1" in phases
        assert "cell2" not in phases

    def test_phase_update_overwrites(self, tmp_path) -> None:
        store = ResultsStore(tmp_path / "test.db")
        store.set_cell_state("cell1", "run1", "running")
        store.set_cell_phase("cell1", "run1", "cloning")
        store.set_cell_phase("cell1", "run1", "harness_running")
        phases = store.get_running_cell_phases("run1")
        assert phases["cell1"][0] == "harness_running"

    def test_phase_isolated_per_run(self, tmp_path) -> None:
        store = ResultsStore(tmp_path / "test.db")
        store.set_cell_state("cell1", "run1", "running")
        store.set_cell_phase("cell1", "run1", "harness_running")
        store.set_cell_state("cell1", "run2", "running")
        store.set_cell_phase("cell1", "run2", "evaluating")
        phases1 = store.get_running_cell_phases("run1")
        phases2 = store.get_running_cell_phases("run2")
        assert phases1["cell1"][0] == "harness_running"
        assert phases2["cell1"][0] == "evaluating"

    def test_migration_adds_phase_columns(self, tmp_path) -> None:
        """A DB created before the phase columns should get them via migration."""
        import sqlite3

        db_path = tmp_path / "test.db"
        # Create a legacy run_state table without phase columns
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE run_state ("
                "cell_id TEXT NOT NULL, run_name TEXT NOT NULL, "
                "status TEXT NOT NULL, started_at TEXT, completed_at TEXT, "
                "error TEXT, PRIMARY KEY (run_name, cell_id))"
            )
            conn.execute(
                "INSERT INTO run_state (cell_id, run_name, status) "
                "VALUES ('cell1', 'run1', 'running')"
            )
            conn.commit()

        # Opening with ResultsStore should add the phase columns
        store = ResultsStore(db_path)
        store.set_cell_phase("cell1", "run1", "harness_running")
        phases = store.get_running_cell_phases("run1")
        assert phases["cell1"][0] == "harness_running"
