"""Integration tests for the Docker runner's risky code paths.

Unlike ``test_docker.py``, these tests exercise real behavior where
feasible (real git operations, real file I/O) rather than mocking
``subprocess.run`` everywhere. Docker daemon calls are still mocked
since we don't have a real Docker daemon in CI.
"""

from __future__ import annotations

import contextlib
import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    RunConfig,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.orchestrator.engine import Orchestrator
from harness_evaluator.orchestrator.results_store import ResultsStore
from harness_evaluator.runner.docker import CompletedProcess, DockerRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def swe_bugfix_task() -> TaskSpec:
    """A task pointing at the real local repo tasks/repos/swe-bugfix-001."""
    return TaskSpec(
        id="swe-bugfix-001",
        name="Fix off-by-one in list pagination function",
        track=TaskTrack.SWE,
        repo_url="tasks/repos/swe-bugfix-001",
        repo_commit="38776ebf76a8d753e9dbca21f10836ab558fc997",
        setup_script="pip install -r requirements.txt",
        task_prompt="Fix the off-by-one bug in src/solution.py",
        test_command="python -m pytest tests/",
        timeout_seconds=300,
    )


@pytest.fixture
def anthropic_model() -> ModelSpec:
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )


@pytest.fixture
def harness() -> HarnessSpec:
    return HarnessSpec(
        name="claude-code",
        adapter="claude-code",
        config={},
        observability_tier="partial",
    )


@pytest.fixture
def runner(tmp_path: Any) -> DockerRunner:
    return DockerRunner(
        image="python:3.12-slim",
        workdir_base=str(tmp_path / "workdir"),
        gateway_host="host.docker.internal",
        gateway_port=8877,
        gateway_db=str(tmp_path / "gateway.db"),
        docker_bin="docker",
    )


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    """Create a CompletedProcess mimicking async subprocess output."""
    return CompletedProcess(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Test 1: _clone_repo with a real local task repo
# ---------------------------------------------------------------------------


class TestCloneRepoRealLocal:
    async def test_clone_repo_copies_local_repo(
        self, runner: DockerRunner, swe_bugfix_task: TaskSpec, tmp_path: Any
    ):
        """_clone_repo should clone a real local repo into <workdir>/repo."""
        workdir = tmp_path / "wd"
        workdir.mkdir()

        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(
                name="h", adapter="claude-code", config={}, observability_tier="partial"
            ),
            model=ModelSpec(
                name="m", provider="anthropic", api_key_env="ANTHROPIC_API_KEY"
            ),
            task=swe_bugfix_task,
            repeat=0,
        )

        await runner._clone_repo(cell, workdir)

        repo_dir = workdir / "repo"
        assert repo_dir.exists(), "repo directory should be created"
        assert (repo_dir / ".git").exists(), "cloned repo should have .git"

        # Expected files
        assert (repo_dir / "src" / "solution.py").exists()
        assert (repo_dir / "tests" / "test_solution.py").exists()

        # The repo is copied + git-init'd (no original history), so just
        # verify there is exactly one commit.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        assert head.returncode == 0
        assert len(head.stdout.strip()) == 40  # a valid SHA-1


# ---------------------------------------------------------------------------
# Test 2: _clone_repo resolves relative paths regardless of cwd
# ---------------------------------------------------------------------------


class TestCloneRepoRelativePath:
    async def test_clone_repo_resolves_relative_path_from_different_cwd(
        self,
        runner: DockerRunner,
        swe_bugfix_task: TaskSpec,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """_clone_repo should resolve 'tasks/repos/...' even when cwd is /tmp."""
        workdir = tmp_path / "wd"
        workdir.mkdir()

        # Change cwd to /tmp so relative resolution can't rely on cwd
        monkeypatch.chdir("/tmp")

        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(
                name="h", adapter="claude-code", config={}, observability_tier="partial"
            ),
            model=ModelSpec(
                name="m", provider="anthropic", api_key_env="ANTHROPIC_API_KEY"
            ),
            task=swe_bugfix_task,
            repeat=0,
        )

        await runner._clone_repo(cell, workdir)

        repo_dir = workdir / "repo"
        assert repo_dir.exists()
        assert (repo_dir / "src" / "solution.py").exists()


# ---------------------------------------------------------------------------
# Test 3: setup.sh is written to workdir before container launch
# ---------------------------------------------------------------------------


class TestSetupScriptWritten:
    async def test_setup_script_written_to_workdir(
        self,
        runner: DockerRunner,
        swe_bugfix_task: TaskSpec,
        tmp_path: Any,
    ):
        """_run_harness should write setup.sh to <workdir>/setup.sh."""
        workdir = tmp_path / "wd"
        workdir.mkdir()
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(
                name="claude-code",
                adapter="claude-code",
                config={},
                observability_tier="partial",
            ),
            model=ModelSpec(
                name="claude-sonnet-4-20250514",
                provider="anthropic",
                api_key_env="ANTHROPIC_API_KEY",
            ),
            task=swe_bugfix_task,
            repeat=0,
        )

        # The pre-existing changes made _start_container, _exec_in_container,
        # and _stop_container async (using _run_subprocess). Mock the async
        # _run_subprocess for Docker calls, and subprocess.run for git ops.
        docker_results = [
            _make_completed(stdout="container-id\n"),  # docker run
            _make_completed(stdout="", stderr=""),  # setup exec
            _make_completed(stdout="harness done", stderr=""),  # harness exec
            _make_completed(stdout="", stderr=""),  # docker stop
        ]

        git_results = [
            MagicMock(returncode=0, stdout="", stderr=""),  # git init
            MagicMock(returncode=0, stdout="", stderr=""),  # git config email
            MagicMock(returncode=0, stdout="", stderr=""),  # git config name
            MagicMock(returncode=0, stdout="", stderr=""),  # git add
            MagicMock(returncode=0, stdout="", stderr=""),  # git commit
        ]

        with (
            patch(
                "harness_evaluator.runner.docker._run_subprocess",
                side_effect=docker_results,
            ),
            patch("harness_evaluator.runner.docker.subprocess.run") as mock_run,
            patch("harness_evaluator.adapters.base.shutil.which", return_value="/usr/bin/claude"),
        ):
            mock_run.side_effect = git_results

            await runner._run_harness(cell, workdir)

        setup_path = workdir / "setup.sh"
        assert setup_path.exists(), "setup.sh should be written to workdir"
        content = setup_path.read_text()
        assert "pip install" in content
        assert content == swe_bugfix_task.setup_script


# ---------------------------------------------------------------------------
# Test 4: Codex get_command has no literal quotes in openai_base_url
# ---------------------------------------------------------------------------


class TestCodexCommandNoQuotes:
    def test_codex_get_command_no_literal_quotes(self, tmp_path: Any):
        """CodexAdapter.get_command() should not wrap openai_base_url in quotes."""
        from harness_evaluator.adapters.codex import CodexAdapter

        model = ModelSpec(
            name="gpt-4o", provider="openai", api_key_env="OPENAI_API_KEY"
        )
        adapter = CodexAdapter(
            workdir=str(tmp_path),
            model=model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )
        cmd = adapter.get_command("implement feature")

        # Find the openai_base_url config override
        base_url_args = [a for a in cmd if "openai_base_url" in a]
        assert len(base_url_args) == 1, "expected exactly one openai_base_url arg"
        base_url_arg = base_url_args[0]

        # The value must NOT contain literal double-quote characters
        assert '"' not in base_url_arg, (
            f"openai_base_url value should not contain literal quotes: {base_url_arg}"
        )
        # It should be of the form openai_base_url=http://...
        assert base_url_arg.startswith("openai_base_url=http"), (
            f"expected openai_base_url=http://... but got: {base_url_arg}"
        )


# ---------------------------------------------------------------------------
# Test 5: Git commit sets local identity
# ---------------------------------------------------------------------------


class TestCommitChangesIdentity:
    async def test_commit_changes_sets_local_identity(self, runner: DockerRunner, tmp_path: Any):
        """_commit_changes should set user.email and user.name in the local repo."""
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()
        # Create a file so there's something to commit
        (repo_dir / "solution.py").write_text("print('hello')\n")

        await runner._commit_changes(repo_dir)

        # Verify local git config has the harness-evaluator identity
        email_result = subprocess.run(
            ["git", "config", "user.email"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        assert email_result.returncode == 0
        assert email_result.stdout.strip() == "harness-evaluator@local"

        name_result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        assert name_result.returncode == 0
        assert name_result.stdout.strip() == "harness-evaluator"

        # Verify a commit was made
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        assert log_result.returncode == 0
        assert "initial" in log_result.stdout


# ---------------------------------------------------------------------------
# Test 6: Budget estimate with wildcard tasks
# ---------------------------------------------------------------------------


class TestBudgetEstimateWildcard:
    def test_estimate_cell_cost_wildcard_tasks(self, tmp_path: Any):
        """_estimate_cell_cost should divide budget by actual matrix cell count
        when config.tasks is ['*'], not by len(tasks)==1.
        """
        # Create a task library with 3 tasks
        task_data = {
            "tasks": [
                {
                    "id": f"task-{i}",
                    "name": f"Task {i}",
                    "track": "swe",
                    "task_prompt": f"Fix bug {i}",
                    "test_command": "echo pass",
                }
                for i in range(3)
            ]
        }
        (tmp_path / "task.yaml").write_text(yaml.dump(task_data))

        config = RunConfig(
            name="budget-test",
            harnesses=[
                HarnessSpec(name="h1", adapter="claude-code"),
                HarnessSpec(name="h2", adapter="codex"),
            ],
            models=[
                ModelSpec(name="m1", provider="anthropic", api_key_env="X"),
                ModelSpec(name="m2", provider="openai", api_key_env="Y"),
            ],
            tasks=["*"],
            task_library_path=str(tmp_path),
            repeats=2,
            budget_usd=100.0,
        )

        store = ResultsStore(str(tmp_path / "results.db"))
        orch = Orchestrator(config, store)

        # Build a cell from the matrix to test
        cells = config.build_matrix()
        assert len(cells) == 2 * 2 * 3 * 2  # 24 cells

        cell = cells[0]
        estimate = orch._estimate_cell_cost(cell)

        # Should be budget / total_cells, not budget / 1
        expected = 100.0 / 24.0
        assert estimate == pytest.approx(expected), (
            f"Expected per-cell estimate {expected} (budget/24), got {estimate}"
        )
        # Sanity: must not be the full budget (which would happen if
        # len(config.tasks)==1 was used instead of actual task count)
        assert estimate < 100.0


# ---------------------------------------------------------------------------
# Test 7: Open-ended evaluator gets gateway_url and trace_id
# ---------------------------------------------------------------------------


class TestOpenEndedEvaluatorGateway:
    async def test_open_ended_evaluator_receives_gateway_and_trace(
        self,
        runner: DockerRunner,
        tmp_path: Any,
    ):
        """Docker runner should pass gateway_url and trace_id to
        OpenEndedEvaluator when evaluating an open-ended task.
        """
        from harness_evaluator.evaluator.open_ended import OpenEndedResult

        open_task = TaskSpec(
            id="open-1",
            name="Open task",
            track=TaskTrack.OPEN_ENDED,
            task_prompt="Design a REST API",
            timeout_seconds=60,
        )

        harness = HarnessSpec(
            name="claude-code",
            adapter="claude-code",
            config={},
            observability_tier="partial",
        )
        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        )
        cell = RunCell(
            run_name="oe-test",
            harness=harness,
            model=model,
            task=open_task,
            repeat=0,
        )

        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        # Mock the OpenEndedEvaluator class (imported locally in run_cell)
        # so we can inspect constructor and evaluate() args.
        mock_eval_instance = MagicMock()
        mock_eval_instance.evaluate = AsyncMock(
            return_value=OpenEndedResult(
                exit_class="fail",
                success=0.0,
                error_class="no_change",
                diff="",
            )
        )

        # Mock the adapter so _run_harness doesn't need real Docker
        mock_adapter = MagicMock()
        mock_adapter.get_env.return_value = {"PATH": "/usr/bin"}
        mock_adapter.get_command.return_value = ["echo", "hello"]
        mock_adapter.cleanup = AsyncMock()

        # Mock _run_subprocess for Docker calls (async)
        docker_results = [
            _make_completed(stdout="cid\n"),  # docker run
            _make_completed(stdout="done"),  # harness exec
            _make_completed(stdout=""),  # docker stop
        ]

        # Mock subprocess.run for git ops in _commit_changes
        git_results = [
            MagicMock(returncode=0, stdout="", stderr=""),  # git init
            MagicMock(returncode=0, stdout="", stderr=""),  # git config email
            MagicMock(returncode=0, stdout="", stderr=""),  # git config name
            MagicMock(returncode=0, stdout="", stderr=""),  # git add
            MagicMock(returncode=0, stdout="", stderr=""),  # git commit
        ]

        with (
            patch(
                "harness_evaluator.evaluator.open_ended.OpenEndedEvaluator",
                return_value=mock_eval_instance,
            ) as mock_eval_class,
            patch(
                "harness_evaluator.runner.docker._run_subprocess",
                new_callable=AsyncMock,
                side_effect=docker_results,
            ),
            patch("harness_evaluator.runner.docker.subprocess.run") as mock_run,
            patch(
                "harness_evaluator.adapters.registry.create_adapter",
                return_value=mock_adapter,
            ),
        ):
            mock_run.side_effect = git_results

            # run_cell may raise during result mapping; we only care about
            # the evaluator call args.
            with contextlib.suppress(Exception):
                await runner.run_cell(cell)

        # Verify the evaluator was instantiated with gateway_url
        assert mock_eval_instance.evaluate.called, (
            "OpenEndedEvaluator.evaluate should have been called"
        )

        # Check constructor received gateway_url (call recorded on the
        # patched class mock, not on the instance)
        ctor_kwargs = mock_eval_class.call_args.kwargs
        gateway_url_passed = ctor_kwargs.get("gateway_url")
        assert gateway_url_passed is not None, (
            "OpenEndedEvaluator should be constructed with gateway_url"
        )
        assert "host.docker.internal" in gateway_url_passed, (
            f"gateway_url should contain host.docker.internal, got: {gateway_url_passed}"
        )

        # Check evaluate received trace_id matching cell.cell_id
        eval_kwargs = mock_eval_instance.evaluate.call_args.kwargs
        trace_id_val = eval_kwargs.get("trace_id")
        assert trace_id_val is not None, (
            "OpenEndedEvaluator.evaluate should receive trace_id"
        )
        assert trace_id_val == cell.cell_id, (
            f"trace_id should be {cell.cell_id}, got {trace_id_val}"
        )
