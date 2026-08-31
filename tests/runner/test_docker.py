"""Tests for the Docker runner.

All ``subprocess.run`` and ``_run_subprocess`` calls are mocked so these
tests do NOT require a real Docker daemon.
"""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.runner.docker import (
    CONTAINER_REPO,
    CONTAINER_WORKSPACE,
    CompletedProcess,
    DockerRunner,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        config={},
        observability_tier="partial",
    )


@pytest.fixture
def cell(harness: HarnessSpec, anthropic_model: ModelSpec, swe_task: TaskSpec) -> RunCell:
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
        docker_bin="docker",
    )


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Create a mock mimicking ``subprocess.CompletedProcess``."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# _build_run_args
# ---------------------------------------------------------------------------


class TestBuildRunArgs:
    def test_basic_args(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(
            workdir, {"FOO": "bar"}, timeout=60, container_name="c1"
        )

        assert args[0] == "docker"
        assert "run" in args
        assert "-d" in args
        assert "--rm" in args
        assert "--name" in args
        assert "c1" in args

    def test_volume_mount(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(
            workdir, {}, timeout=60, container_name="c1"
        )
        # The workdir must be mounted at /workspace
        mount = f"{workdir.resolve()}:{CONTAINER_WORKSPACE}"
        assert "-v" in args
        assert mount in args

    def test_workdir_set_to_workspace(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(
            workdir, {}, timeout=60, container_name="c1"
        )
        assert "-w" in args
        assert CONTAINER_WORKSPACE in args

    def test_env_vars_passed_via_env_flag(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        env = {"ANTHROPIC_API_KEY": "sk-secret", "HARNESS_EVALUATOR_TRACE_ID": "cell-1"}
        args = runner._build_run_args(workdir, env, timeout=60, container_name="c1")

        # Each env var must appear as --env KEY=VALUE
        assert "--env" in args
        assert "ANTHROPIC_API_KEY=sk-secret" in args
        assert "HARNESS_EVALUATOR_TRACE_ID=cell-1" in args

    def test_add_host_for_gateway(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(
            workdir, {}, timeout=60, container_name="c1"
        )
        assert "--add-host" in args
        assert "host.docker.internal:host-gateway" in args

    def test_host_network_fallback(self, tmp_path: Any):
        r = DockerRunner(use_host_network=True, workdir_base=str(tmp_path))
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = r._build_run_args(workdir, {}, timeout=60, container_name="c1")
        assert "--network" in args
        assert "host" in args
        assert "--add-host" not in args

    def test_stop_timeout_set(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(workdir, {}, timeout=42, container_name="c1")
        assert "--stop-timeout" in args
        assert "42" in args

    def test_memory_and_cpu_limits(self, tmp_path: Any):
        r = DockerRunner(
            memory_limit="2g",
            cpu_limit="2.0",
            workdir_base=str(tmp_path),
        )
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = r._build_run_args(workdir, {}, timeout=60, container_name="c1")
        assert "--memory" in args
        assert "2g" in args
        assert "--cpus" in args
        assert "2.0" in args

    def test_image_and_sleep_appended(self, runner: DockerRunner, tmp_path: Any):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(workdir, {}, timeout=60, container_name="c1")
        assert "python:3.12-slim" in args
        assert "sleep" in args
        # sleep duration should exceed the timeout
        sleep_idx = args.index("sleep")
        sleep_val = int(args[sleep_idx + 1])
        assert sleep_val > 60


# ---------------------------------------------------------------------------
# Container lifecycle: start, exec, stop
# ---------------------------------------------------------------------------


class TestContainerLifecycle:
    async def test_start_container_returns_id(
        self, runner: DockerRunner, tmp_path: Any
    ):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = CompletedProcess(
                returncode=0, stdout="abc123\n", stderr=""
            )
            cid = await runner._start_container(
                workdir, {}, timeout=60, name="c1"
            )
        assert cid == "abc123"
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "docker"
        assert "run" in cmd

    async def test_start_container_failure_raises(
        self, runner: DockerRunner, tmp_path: Any
    ):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = CompletedProcess(
                returncode=1, stdout="", stderr="docker daemon not running"
            )
            with pytest.raises(RuntimeError, match="docker run failed"):
                await runner._start_container(
                    workdir, {}, timeout=60, name="c1"
                )

    async def test_exec_in_container_captures_output(
        self, runner: DockerRunner
    ):
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = CompletedProcess(
                returncode=0, stdout="hello", stderr=""
            )
            result = await runner._exec_in_container(
                "cid", ["echo", "hello"], timeout=30
            )
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.timed_out is False

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "-w" in cmd
        assert CONTAINER_WORKSPACE in cmd
        assert "cid" in cmd
        assert "echo" in cmd

    async def test_exec_in_container_with_cwd(self, runner: DockerRunner):
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = CompletedProcess(
                returncode=0, stdout="ok", stderr=""
            )
            await runner._exec_in_container(
                "cid", ["pwd"], timeout=30, cwd=CONTAINER_REPO
            )
        cmd = mock_run.call_args[0][0]
        assert "-w" in cmd
        assert CONTAINER_REPO in cmd
        # Should NOT contain the default workspace as a separate arg
        ws_idx = cmd.index("-w")
        assert cmd[ws_idx + 1] == CONTAINER_REPO

    async def test_exec_in_container_timeout(self, runner: DockerRunner):
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="docker", timeout=30
            )
            result = await runner._exec_in_container(
                "cid", ["sleep", "999"], timeout=30
            )
        assert result.timed_out is True
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()

    async def test_stop_container_calls_docker_stop(
        self, runner: DockerRunner
    ):
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = CompletedProcess(
                returncode=0, stdout="", stderr=""
            )
            await runner._stop_container("cid")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "stop" in cmd
        assert "cid" in cmd

    async def test_stop_container_swallows_errors(
        self, runner: DockerRunner
    ):
        with patch(
            "harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="docker", timeout=30
            )
            # Should not raise
            await runner._stop_container("cid")


# ---------------------------------------------------------------------------
# Full _run_harness flow with mocked subprocess
# ---------------------------------------------------------------------------


class TestRunHarness:
    @patch("harness_evaluator.runner.docker.subprocess.run")
    @patch("harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock)
    async def test_run_harness_full_flow(
        self,
        mock_run_async: AsyncMock,
        mock_subprocess: MagicMock,
        runner: DockerRunner,
        cell: RunCell,
        tmp_path: Any,
    ):
        # Prepare workdir with a repo so git ops work
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        # _run_subprocess is called for: docker run, docker exec (harness),
        # docker stop. subprocess.run is called for git ops (no setup_script
        # on this task, no .git in repo_dir so init + config + add + commit).
        mock_run_async.side_effect = [
            CompletedProcess(
                returncode=0, stdout="container-id-1\n", stderr=""
            ),  # docker run
            CompletedProcess(
                returncode=0, stdout="harness output", stderr=""
            ),  # docker exec harness
            CompletedProcess(
                returncode=0, stdout="", stderr=""
            ),  # docker stop
        ]
        # git init / config email / config name / add / commit
        mock_subprocess.side_effect = [
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
        ]

        result = await runner._run_harness(cell, workdir)

        assert result.exit_code == 0
        assert result.stdout == "harness output"
        assert result.timed_out is False

        # Verify docker run was called via _run_subprocess
        run_calls = mock_run_async.call_args_list
        first_cmd = run_calls[0][0][0]
        assert first_cmd[0] == "docker"
        assert "run" in first_cmd

        # Verify docker exec was called for the harness
        exec_cmds = [
            c[0][0] for c in run_calls if "exec" in c[0][0]
        ]
        assert len(exec_cmds) >= 1

        # Verify docker stop was called
        stop_cmds = [c[0][0] for c in run_calls if "stop" in c[0][0]]
        assert len(stop_cmds) >= 1

    @patch("harness_evaluator.runner.docker.subprocess.run")
    @patch("harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock)
    async def test_run_harness_timeout_kills_container(
        self,
        mock_run_async: AsyncMock,
        mock_subprocess: MagicMock,
        runner: DockerRunner,
        cell: RunCell,
    ):
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        mock_run_async.side_effect = [
            CompletedProcess(
                returncode=0, stdout="container-id-2\n", stderr=""
            ),  # docker run
            # docker exec harness times out
            subprocess.TimeoutExpired(cmd="docker exec", timeout=60),
            CompletedProcess(
                returncode=0, stdout="", stderr=""
            ),  # docker stop
        ]
        # git init / config email / config name / add / commit
        mock_subprocess.side_effect = [
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
        ]

        # A harness timeout now raises RetryableError so the orchestrator
        # can retry with backoff instead of silently scoring as NO_CHANGE.
        from harness_evaluator.orchestrator.engine import RetryableError

        with pytest.raises(RetryableError, match="timed out"):
            await runner._run_harness(cell, workdir)

        # Ensure docker stop was still called
        stop_cmds = [
            c[0][0]
            for c in mock_run_async.call_args_list
            if "stop" in c[0][0]
        ]
        assert len(stop_cmds) >= 1

    @patch("harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock)
    async def test_run_harness_no_adapter(
        self,
        mock_run_async: AsyncMock,
        runner: DockerRunner,
        tmp_path: Any,
    ):
        # Build a cell with a nonexistent adapter
        harness = HarnessSpec(
            name="nope", adapter="nonexistent", config={}, observability_tier="partial"
        )
        model = ModelSpec(
            name="m", provider="anthropic", api_key_env="ANTHROPIC_API_KEY"
        )
        task = TaskSpec(
            id="t", name="t", track=TaskTrack.SWE, task_prompt="do thing"
        )
        cell = RunCell(run_name="r", harness=harness, model=model, task=task, repeat=0)

        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        # No docker calls should happen since adapter is None
        result = await runner._run_harness(cell, workdir)

        assert result.exit_code == -1
        assert "No adapter found" in result.stderr
        # No docker run should have been invoked
        run_cmds = [
            c[0][0] for c in mock_run_async.call_args_list if "run" in c[0][0]
        ]
        assert len(run_cmds) == 0


# ---------------------------------------------------------------------------
# Env var / secret injection
# ---------------------------------------------------------------------------


class TestSecretInjection:
    def test_only_allowlisted_env_passed(
        self, runner: DockerRunner, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        # Set a host env var that should NOT leak into the container
        monkeypatch.setenv("HOST_SECRET", "top-secret")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        monkeypatch.setenv("PATH", "/usr/bin")

        workdir = tmp_path / "wd"
        workdir.mkdir()

        from harness_evaluator.adapters.registry import create_adapter

        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        )
        adapter = create_adapter(
            "claude-code",
            workdir=str(workdir),
            model=model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )
        assert adapter is not None
        env = adapter.get_env()

        # HOST_SECRET must not be present
        assert "HOST_SECRET" not in env
        # API key should be injected
        assert env["ANTHROPIC_API_KEY"] == "sk-test-key"
        # Gateway URL should use host.docker.internal, not 127.0.0.1
        assert "host.docker.internal" in env["ANTHROPIC_BASE_URL"]
        assert "127.0.0.1" not in env["ANTHROPIC_BASE_URL"]

    def test_env_passed_to_docker_run(
        self, runner: DockerRunner, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        monkeypatch.setenv("PATH", "/usr/bin")

        workdir = tmp_path / "wd"
        workdir.mkdir()

        from harness_evaluator.adapters.registry import create_adapter

        model = ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        )
        adapter = create_adapter(
            "claude-code",
            workdir=str(workdir),
            model=model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )
        assert adapter is not None
        env = adapter.get_env()

        args = runner._build_run_args(workdir, env, timeout=60, container_name="c1")
        # The API key must appear as --env, not baked in elsewhere
        assert "ANTHROPIC_API_KEY=sk-test-key" in args
        # HOST_SECRET should never appear
        assert not any("HOST_SECRET" in a for a in args)


# ---------------------------------------------------------------------------
# Gateway reachability
# ---------------------------------------------------------------------------


class TestGatewayReachability:
    def test_gateway_url_uses_docker_host(self, runner: DockerRunner, cell: RunCell):
        # The gateway URL constructed in _run_harness should use
        # host.docker.internal, not 127.0.0.1
        assert runner.gateway_host == "host.docker.internal"

    def test_add_host_enables_gateway_from_container(
        self, runner: DockerRunner, tmp_path: Any
    ):
        workdir = tmp_path / "wd"
        workdir.mkdir()
        args = runner._build_run_args(workdir, {}, timeout=60, container_name="c1")
        # host.docker.internal must be resolvable inside the container
        assert "host.docker.internal:host-gateway" in args


# ---------------------------------------------------------------------------
# Workdir preservation
# ---------------------------------------------------------------------------


class TestWorkdirPreservation:
    @patch("harness_evaluator.runner.docker.subprocess.run")
    @patch("harness_evaluator.runner.docker._run_subprocess", new_callable=AsyncMock)
    async def test_workdir_mounted_and_persists(
        self,
        mock_run_async: AsyncMock,
        mock_subprocess: MagicMock,
        runner: DockerRunner,
        cell: RunCell,
        tmp_path: Any,
    ):
        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        mock_run_async.side_effect = [
            CompletedProcess(
                returncode=0, stdout="cid-mount\n", stderr=""
            ),  # docker run
            CompletedProcess(
                returncode=0, stdout="done", stderr=""
            ),  # docker exec
            CompletedProcess(
                returncode=0, stdout="", stderr=""
            ),  # docker stop
        ]
        # git init / config email / config name / add / commit
        mock_subprocess.side_effect = [
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
            _make_completed(returncode=0),
        ]

        await runner._run_harness(cell, workdir)

        # The docker run command must mount the workdir
        run_cmd = mock_run_async.call_args_list[0][0][0]
        mount_arg = f"{workdir.resolve()}:{CONTAINER_WORKSPACE}"
        assert mount_arg in run_cmd

        # The workdir must still exist on the host after the run
        assert workdir.exists()
        assert repo_dir.exists()


# ---------------------------------------------------------------------------
# Adapter get_command integration
# ---------------------------------------------------------------------------


class TestAdapterGetCommand:
    def test_base_adapter_get_command_raises(self, tmp_path: Any):
        from harness_evaluator.adapters.base import BaseAdapter

        # Cannot instantiate ABC directly; create a minimal subclass
        class DummyAdapter(BaseAdapter):
            @staticmethod
            def info():
                from harness_evaluator.adapters.base import AdapterInfo

                return AdapterInfo(
                    name="dummy",
                    display_name="Dummy",
                    observability_tier="minimal",
                    description="dummy",
                )

            async def prepare(self):
                pass

            async def run(self, task_prompt, timeout=600):
                pass

        model = ModelSpec(name="m", provider="anthropic", api_key_env="X")
        adapter = DummyAdapter(workdir=str(tmp_path), model=model)
        with pytest.raises(NotImplementedError, match="get_command"):
            adapter.get_command("do something")

    def test_claude_code_get_command(self, tmp_path: Any, anthropic_model: ModelSpec):
        from harness_evaluator.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path),
            model=anthropic_model,
            gateway_url="http://host.docker.internal:8877",
            trace_id="cell-1",
        )
        cmd = adapter.get_command("fix the bug")
        assert "claude" in cmd[0]
        assert "-p" in cmd
        assert "fix the bug" in cmd
        assert "--model" in cmd

    def test_claude_code_skip_permissions_by_default(
        self, tmp_path: Any, anthropic_model: ModelSpec
    ):
        from harness_evaluator.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path),
            model=anthropic_model,
        )
        cmd = adapter.get_command("fix the bug")
        assert "--dangerously-skip-permissions" in cmd

    def test_claude_code_skip_permissions_disabled(
        self, tmp_path: Any, anthropic_model: ModelSpec
    ):
        from harness_evaluator.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path),
            model=anthropic_model,
            config={"dangerously_skip_permissions": False},
        )
        cmd = adapter.get_command("fix the bug")
        assert "--dangerously-skip-permissions" not in cmd

    def test_claude_code_allowed_tools_without_skip_permissions(
        self, tmp_path: Any, anthropic_model: ModelSpec
    ):
        from harness_evaluator.adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(
            workdir=str(tmp_path),
            model=anthropic_model,
            config={
                "dangerously_skip_permissions": False,
                "allowed_tools": ["Read", "Write", "Edit"],
            },
        )
        cmd = adapter.get_command("fix the bug")
        assert "--dangerously-skip-permissions" not in cmd
        assert "--allowedTools" in cmd
        assert "Read,Write,Edit" in cmd

    def test_codex_get_command(self, tmp_path: Any):
        from harness_evaluator.adapters.codex import CodexAdapter

        model = ModelSpec(name="gpt-4o", provider="openai", api_key_env="OPENAI_API_KEY")
        adapter = CodexAdapter(
            workdir=str(tmp_path),
            model=model,
            gateway_url="http://host.docker.internal:8877",
        )
        cmd = adapter.get_command("implement feature")
        assert "codex" in cmd[0]
        assert "exec" in cmd
        assert "implement feature" in cmd

    def test_opencode_get_command(self, tmp_path: Any, anthropic_model: ModelSpec):
        from harness_evaluator.adapters.opencode import OpenCodeAdapter

        adapter = OpenCodeAdapter(
            workdir=str(tmp_path), model=anthropic_model
        )
        cmd = adapter.get_command("refactor code")
        assert "opencode" in cmd[0]
        assert "run" in cmd
        assert "refactor code" in cmd
        assert "--model" in cmd

    def test_pi_get_command(self, tmp_path: Any, anthropic_model: ModelSpec):
        from harness_evaluator.adapters.pi import PiAdapter

        adapter = PiAdapter(workdir=str(tmp_path), model=anthropic_model)
        cmd = adapter.get_command("write tests")
        assert "pi" in cmd[0]
        assert "-p" in cmd
        assert "write tests" in cmd

    def test_omp_get_command(self, tmp_path: Any, anthropic_model: ModelSpec):
        from harness_evaluator.adapters.omp import OMPAdapter

        adapter = OMPAdapter(workdir=str(tmp_path), model=anthropic_model)
        cmd = adapter.get_command("write tests")
        assert "omp" in cmd[0]
        assert "-p" in cmd
        assert "write tests" in cmd
