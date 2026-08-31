"""Tests for reconciliation wiring in the Docker runner."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.runner.docker import (
    CompletedProcess,
    DockerRunner,
    RunResult,
)


@pytest.fixture
def anthropic_model() -> ModelSpec:
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )


@pytest.fixture
def swe_task() -> TaskSpec:
    return TaskSpec(
        id="t1",
        name="Test task",
        track=TaskTrack.SWE,
        task_prompt="Fix the bug in foo.py",
        test_command="python -m pytest",
        timeout_seconds=60,
    )


@pytest.fixture
def harness() -> HarnessSpec:
    return HarnessSpec(
        name="claude-code",
        adapter="claude-code",
        config={"output_format": "json"},
        observability_tier="partial",
    )


@pytest.fixture
def cell(
    harness: HarnessSpec,
    anthropic_model: ModelSpec,
    swe_task: TaskSpec,
) -> RunCell:
    return RunCell(
        run_name="test-run",
        harness=harness,
        model=anthropic_model,
        task=swe_task,
        repeat=0,
    )


@pytest.fixture
def runner(tmp_path: Any) -> DockerRunner:
    return DockerRunner(
        image="python:3.12-slim",
        workdir_base=str(tmp_path / "workdir"),
        gateway_host="host.docker.internal",
        gateway_port=8877,
        gateway_db=str(tmp_path / "gateway.db"),
        results_db=str(tmp_path / "results.db"),
        docker_bin="docker",
    )


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestReconcileCell:
    """Unit tests for DockerRunner._reconcile_cell()."""

    def test_returns_none_when_adapter_reports_none(
        self, runner: DockerRunner, cell: RunCell, tmp_path: Any
    ):
        """When the harness does not report usage, reconciliation is skipped."""
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        harness_result = RunResult(
            exit_code=0,
            stdout="plain text, no usage",
            stderr="",
            timed_out=False,
            workdir=str(workdir),
            duration_ms=1000.0,
        )
        proxy_usage = TokenUsage(input_tokens=100, output_tokens=50)

        summary = runner._reconcile_cell(cell, harness_result, proxy_usage)
        assert summary is None

    def test_returns_summary_when_usage_matches(
        self, runner: DockerRunner, cell: RunCell, tmp_path: Any
    ):
        """When proxy and self-report match, summary has matched=True."""
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        stdout = json.dumps(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
            }
        )
        harness_result = RunResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            timed_out=False,
            workdir=str(workdir),
            duration_ms=1000.0,
        )
        proxy_usage = TokenUsage(input_tokens=100, output_tokens=50)

        summary = runner._reconcile_cell(cell, harness_result, proxy_usage)
        assert summary is not None
        assert summary["matched"] is True
        assert summary["max_discrepancy_pct"] == 0.0
        assert summary["details"] == {}

    def test_returns_summary_when_usage_discrepancy(
        self, runner: DockerRunner, cell: RunCell, tmp_path: Any
    ):
        """When proxy and self-report disagree, summary has matched=False."""
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        stdout = json.dumps(
            {
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 50,
                },
            }
        )
        harness_result = RunResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            timed_out=False,
            workdir=str(workdir),
            duration_ms=1000.0,
        )
        proxy_usage = TokenUsage(input_tokens=100, output_tokens=50)

        summary = runner._reconcile_cell(cell, harness_result, proxy_usage)
        assert summary is not None
        assert summary["matched"] is False
        assert summary["max_discrepancy_pct"] > 2.0
        assert "self_report.input_tokens" in summary["details"]

    def test_persists_to_results_store(
        self, runner: DockerRunner, cell: RunCell, tmp_path: Any
    ):
        """The reconciliation result is saved to the results DB."""
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        stdout = json.dumps(
            {"usage": {"input_tokens": 100, "output_tokens": 50}}
        )
        harness_result = RunResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            timed_out=False,
            workdir=str(workdir),
            duration_ms=1000.0,
        )
        proxy_usage = TokenUsage(input_tokens=100, output_tokens=50)

        runner._reconcile_cell(cell, harness_result, proxy_usage)

        from harness_evaluator.orchestrator.results_store import ResultsStore

        store = ResultsStore(runner.results_db)
        result = store.get_reconciliation_result(cell.cell_id)
        assert result is not None
        assert result["matched"] == 1

    def test_returns_none_for_nonexistent_adapter(
        self, runner: DockerRunner, tmp_path: Any
    ):
        """When the adapter does not exist, reconciliation is skipped."""
        harness = HarnessSpec(
            name="nope",
            adapter="nonexistent",
            config={},
            observability_tier="minimal",
        )
        model = ModelSpec(
            name="m", provider="anthropic", api_key_env="ANTHROPIC_API_KEY"
        )
        task = TaskSpec(
            id="t", name="t", track=TaskTrack.SWE, task_prompt="do thing"
        )
        cell = RunCell(
            run_name="r", harness=harness, model=model, task=task, repeat=0
        )
        harness_result = RunResult(
            exit_code=0,
            stdout="output",
            stderr="",
            timed_out=False,
            workdir=str(tmp_path),
            duration_ms=1000.0,
        )
        proxy_usage = TokenUsage(input_tokens=100, output_tokens=50)

        summary = runner._reconcile_cell(cell, harness_result, proxy_usage)
        assert summary is None


class TestRunCellReconciliation:
    """Integration tests: run_cell includes reconciliation in harness_metadata."""

    @patch("harness_evaluator.runner.docker.subprocess.run")
    @patch("harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock)
    async def test_run_cell_includes_reconciliation_metadata(
        self,
        mock_run_async: AsyncMock,
        mock_subprocess: MagicMock,
        runner: DockerRunner,
        cell: RunCell,
        tmp_path: Any,
    ):
        """run_cell populates harness_metadata['reconciliation'] when
        the harness reports usage."""
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        claude_json = json.dumps(
            {
                "type": "result",
                "num_turns": 1,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
            }
        )

        # Seed the gateway DB so num_api_calls > 0 and reconciliation runs.
        # The runner deletes prior calls by trace_id before executing, so we
        # patch delete_by_trace to preserve our seeded call.
        from harness_evaluator.gateway.models import (
            CapturedCall,
            CostBreakdown,
            Provider,
        )
        from harness_evaluator.gateway.models import (
            TokenUsage as GWTokenUsage,
        )
        from harness_evaluator.gateway.store import CallStore

        gw_store = CallStore(str(runner.gateway_db))
        gw_store.save(
            CapturedCall(
                id="test-call-1",
                trace_id=cell.cell_id,
                provider=Provider.ANTHROPIC,
                model="claude-sonnet-4-20250514",
                usage=GWTokenUsage(input_tokens=100, output_tokens=50),
                cost=CostBreakdown(),
                method="POST",
                path="/v1/messages",
                response_status=200,
            )
        )

        mock_run_async.side_effect = [
            CompletedProcess(
                returncode=0, stdout="container-id-recon\n", stderr=""
            ),  # docker run
            CompletedProcess(
                returncode=0, stdout=claude_json, stderr=""
            ),  # docker exec harness
            CompletedProcess(
                returncode=0, stdout="", stderr=""
            ),  # docker stop
        ]
        mock_subprocess.side_effect = [
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
        ]

        # Mock the SWEEvaluator so we don't run real git/pytest.
        from harness_evaluator.evaluator.swe import (
            ErrorClass,
            EvaluationResult,
            SWEEvaluator,
        )

        mock_eval = MagicMock(spec=SWEEvaluator)
        mock_eval.evaluate.return_value = EvaluationResult(
            exit_class="pass",
            success=1.0,
            error_class=ErrorClass.SUCCESS,
            diff="diff --git a/foo.py b/foo.py\n",
        )

        with patch(
            "harness_evaluator.evaluator.swe.SWEEvaluator", return_value=mock_eval
        ), patch(
            "harness_evaluator.gateway.store.CallStore.delete_by_trace",
            return_value=None,
        ):
            result = await runner.run_cell(cell)

        recon = result["harness_metadata"].get("reconciliation")
        assert recon is not None
        assert "matched" in recon
        assert "max_discrepancy_pct" in recon
        assert "details" in recon

    @patch("harness_evaluator.runner.docker.subprocess.run")
    @patch("harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock)
    async def test_run_cell_reconciliation_none_for_minimal_harness(
        self,
        mock_run_async: AsyncMock,
        mock_subprocess: MagicMock,
        runner: DockerRunner,
        tmp_path: Any,
    ):
        """run_cell sets reconciliation to None for harnesses that don't
        report usage (e.g. Pi)."""
        harness = HarnessSpec(
            name="pi",
            adapter="pi",
            config={},
            observability_tier="minimal",
        )
        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        )
        task = TaskSpec(
            id="t1",
            name="Test task",
            track=TaskTrack.SWE,
            task_prompt="Fix the bug",
            test_command="python -m pytest",
            timeout_seconds=60,
        )
        cell = RunCell(
            run_name="test-run",
            harness=harness,
            model=model,
            task=task,
            repeat=0,
        )

        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        mock_run_async.side_effect = [
            CompletedProcess(
                returncode=0, stdout="container-id-pi\n", stderr=""
            ),
            CompletedProcess(
                returncode=0, stdout="pi output no tokens", stderr=""
            ),
            CompletedProcess(
                returncode=0, stdout="", stderr=""
            ),
        ]
        mock_subprocess.side_effect = [
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
        ]

        from harness_evaluator.evaluator.swe import (
            ErrorClass,
            EvaluationResult,
            SWEEvaluator,
        )

        mock_eval = MagicMock(spec=SWEEvaluator)
        mock_eval.evaluate.return_value = EvaluationResult(
            exit_class="fail",
            success=0.0,
            error_class=ErrorClass.NO_CHANGE,
        )

        with patch(
            "harness_evaluator.evaluator.swe.SWEEvaluator", return_value=mock_eval
        ):
            result = await runner.run_cell(cell)

        assert result["harness_metadata"]["reconciliation"] is None
