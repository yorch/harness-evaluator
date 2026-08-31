"""Integration tests verifying adapter commands reach docker exec.

These tests mock ``_run_subprocess`` (the async Docker CLI wrapper) and
``subprocess.run`` (host-side git ops) so no real Docker daemon is needed.
The adapter is created via the real ``create_adapter`` registry so that
``get_command()`` produces the actual command list the runner sends to
``docker exec``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness_evaluator.adapters.codex import CodexAdapter
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.runner.docker import CompletedProcess, DockerRunner


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


def _make_completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CompletedProcess:
    return CompletedProcess(returncode=returncode, stdout=stdout, stderr=stderr)


def _git_results() -> list[MagicMock]:
    return [
        MagicMock(returncode=0, stdout="", stderr=""),  # git init
        MagicMock(returncode=0, stdout="", stderr=""),  # git config email
        MagicMock(returncode=0, stdout="", stderr=""),  # git config name
        MagicMock(returncode=0, stdout="", stderr=""),  # git add
        MagicMock(returncode=0, stdout="", stderr=""),  # git commit
    ]


class TestCodexCommandReachesDockerExec:
    async def test_codex_command_reaches_docker_exec_with_setup_script(
        self, runner: DockerRunner, tmp_path: Any
    ) -> None:
        """With a setup_script, the runner makes two exec calls: setup then harness.

        The FIRST exec must be ``bash /workspace/setup.sh`` (the setup script)
        and the SECOND exec must be the exact ``CodexAdapter.get_command()``
        output. This guards against the runner accidentally sending the setup
        command to the harness or vice-versa.
        """
        task = TaskSpec(
            id="setup-1",
            name="Setup task",
            track=TaskTrack.SWE,
            task_prompt="implement feature",
            setup_script="pip install -e .",
            test_command="python -m pytest",
            timeout_seconds=60,
        )
        harness = HarnessSpec(
            name="codex",
            adapter="codex",
            config={},
            observability_tier="partial",
        )
        model = ModelSpec(
            name="gpt-4o",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
        )
        cell = RunCell(
            run_name="setup-test",
            harness=harness,
            model=model,
            task=task,
            repeat=0,
        )

        workdir = runner.workdir_base / cell.cell_id
        workdir.mkdir(parents=True, exist_ok=True)
        repo_dir = workdir / "repo"
        repo_dir.mkdir()

        container_id = "setup-container-id"
        docker_results = [
            _make_completed(stdout=f"{container_id}\n"),  # docker run
            _make_completed(stdout="setup done"),  # setup exec
            _make_completed(stdout="codex output"),  # harness exec
            _make_completed(stdout=""),  # docker stop
        ]

        with (
            patch(
                "harness_evaluator.runner.docker._run_subprocess",
                new_callable=AsyncMock,
                side_effect=docker_results,
            ) as mock_subproc,
            patch(
                "harness_evaluator.runner.docker.subprocess.run"
            ) as mock_run,
            patch(
                "harness_evaluator.adapters.base.shutil.which",
                return_value="/usr/bin/codex",
            ),
        ):
            mock_run.side_effect = _git_results()
            await runner._run_harness(cell, workdir)

        # Collect all docker exec calls (commands containing "exec" at idx 1).
        all_cmds = [c[0][0] for c in mock_subproc.call_args_list]
        exec_cmds = [cmd for cmd in all_cmds if "exec" in cmd]

        assert len(exec_cmds) == 2, (
            f"expected exactly 2 exec calls, got {len(exec_cmds)}: {exec_cmds}"
        )

        # The command portion is everything after the container_id in the
        # exec args: [docker, exec, -w, <cwd>, <container_id>, *command]
        first_cmd = exec_cmds[0]
        second_cmd = exec_cmds[1]

        first_command = first_cmd[first_cmd.index(container_id) + 1:]
        second_command = second_cmd[second_cmd.index(container_id) + 1:]

        # FIRST exec must be the setup script.
        assert first_command == ["bash", "/workspace/setup.sh"], (
            f"first exec should be setup script, got: {first_command}"
        )

        # SECOND exec must match CodexAdapter.get_command() exactly.
        expected_adapter = CodexAdapter(
            workdir=str(workdir),
            model=model,
            gateway_url=(
                f"http://{runner.gateway_host}:{runner.gateway_port}"
            ),
            trace_id=cell.cell_id,
        )
        expected_harness_cmd = expected_adapter.get_command(task.task_prompt)
        assert second_command == expected_harness_cmd, (
            f"second exec should match CodexAdapter.get_command(), "
            f"got: {second_command}, expected: {expected_harness_cmd}"
        )
