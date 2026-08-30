"""Tests for the static report generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessbench.orchestrator.config import HarnessSpec, ModelSpec, RunCell, TaskSpec, TaskTrack
from harnessbench.orchestrator.results_store import ResultsStore
from harnessbench.reporting.static_report import ReportGenerator


@pytest.fixture
def store_with_results(tmp_path):
    store = ResultsStore(str(tmp_path / "test_results.db"))

    # Add results for 2 harnesses × 1 model × 2 tasks × 2 repeats
    for harness_name in ["opencode", "claude-code"]:
        for task_id in ["task-1", "task-2"]:
            for repeat in range(2):
                cell = RunCell(
                    run_name="test-run",
                    harness=HarnessSpec(name=harness_name, adapter=harness_name),
                    model=ModelSpec(
                        name="claude-sonnet-4-20250514",
                        provider="anthropic",
                        api_key_env="X",
                    ),
                    task=TaskSpec(
                        id=task_id,
                        name=f"Task {task_id}",
                        track=TaskTrack.SWE,
                        task_prompt="Fix bug",
                    ),
                    repeat=repeat,
                )
                store.save_result(
                    cell=cell,
                    exit_class="pass" if harness_name == "opencode" else "fail",
                    success=1.0 if harness_name == "opencode" else 0.0,
                    total_cost=0.001 if harness_name == "opencode" else 0.002,
                    latency_ms=5000 if harness_name == "opencode" else 10000,
                    num_api_calls=3,
                )

    return store


class TestReportGenerator:
    def test_generate_html(self, store_with_results, tmp_path):
        gen = ReportGenerator(store_with_results)
        paths = gen.generate("test-run", tmp_path / "reports")

        assert "html" in paths
        assert Path(paths["html"]).exists()
        html = Path(paths["html"]).read_text()
        assert "test-run" in html
        assert "opencode" in html
        assert "claude-code" in html

    def test_generate_json(self, store_with_results, tmp_path):
        gen = ReportGenerator(store_with_results)
        paths = gen.generate("test-run", tmp_path / "reports")

        assert "json" in paths
        assert Path(paths["json"]).exists()
        data = json.loads(Path(paths["json"]).read_text())
        assert data["run_name"] == "test-run"
        assert data["total_cells"] == 8
        assert "leaderboards" in data

    def test_generate_csv(self, store_with_results, tmp_path):
        gen = ReportGenerator(store_with_results)
        paths = gen.generate("test-run", tmp_path / "reports")

        assert "csv" in paths
        assert Path(paths["csv"]).exists()
        content = Path(paths["csv"]).read_text()
        assert "opencode" in content
        assert "claude-code" in content

    def test_leaderboard_sorted_by_success(self, store_with_results, tmp_path):
        gen = ReportGenerator(store_with_results)
        results = store_with_results.get_all_results("test-run")
        leaderboards = gen._build_leaderboards(results)

        # Should have one model entry
        assert "claude-sonnet-4-20250514" in leaderboards
        rows = leaderboards["claude-sonnet-4-20250514"]

        # opencode (100% success) should be first, claude-code (0%) second
        assert rows[0]["harness"] == "opencode"
        assert rows[1]["harness"] == "claude-code"
        assert float(rows[0]["success_pct"]) > float(rows[1]["success_pct"])
