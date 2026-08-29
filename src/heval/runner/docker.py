"""Docker-based runner: executes harnesses in isolated containers.

Each run gets a fresh container with:
  - The task repo cloned and set up on the host (mounted as a volume)
  - The harness installed and configured
  - The gateway proxy accessible for token accounting
  - Network policy enforced
  - Timeout enforcement

The runner uses Approach A: it launches a long-running container
(``docker run -d ... sleep``), then runs setup and the harness command
inside it via ``docker exec``, and finally stops the container.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heval.adapters.base import AdapterResult
from heval.orchestrator.config import RunCell
from heval.orchestrator.engine import RetryableError

logger = logging.getLogger(__name__)

# Workspace path inside the container (workdir is mounted here).
CONTAINER_WORKSPACE = "/workspace"

# Repo subdirectory inside the container workspace.
CONTAINER_REPO = "/workspace/repo"


@dataclass
class CompletedProcess:
    """Result from an async subprocess execution.

    Mimics ``subprocess.CompletedProcess`` so callers can use the same
    ``returncode``, ``stdout``, ``stderr`` attributes.
    """

    returncode: int
    stdout: str
    stderr: str


async def _run_subprocess(
    args: list[str], timeout: int | None = None
) -> CompletedProcess:
    """Run a subprocess asynchronously and return a CompletedProcess.

    Uses ``asyncio.create_subprocess_exec`` so the event loop is not
    blocked while waiting for the process to finish.

    Raises ``subprocess.TimeoutExpired`` if the process does not finish
    within ``timeout`` seconds.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise subprocess.TimeoutExpired(
            cmd=args, timeout=timeout if timeout is not None else 0
        ) from None
    returncode = proc.returncode if proc.returncode is not None else -1
    return CompletedProcess(
        returncode=returncode,
        stdout=stdout_bytes.decode(errors="replace") if stdout_bytes else "",
        stderr=stderr_bytes.decode(errors="replace") if stderr_bytes else "",
    )


@dataclass
class RunResult:
    """Raw result from running a harness in a container."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    workdir: str
    duration_ms: float


class DockerRunner:
    """Runs harnesses in Docker containers.

    The runner performs the following lifecycle per cell:

    1. Creates a host workdir and clones/sets up the task repo there.
    2. Launches a detached container with the workdir mounted as a volume
       at ``/workspace`` and a long-running ``sleep`` so we can ``exec``
       into it.
    3. Runs any setup script inside the container via ``docker exec``.
    4. Runs the harness command (from the adapter's ``get_command``)
       inside the container via ``docker exec``, with a timeout.
    5. Captures stdout/stderr and stops the container.
    6. Evaluates results on the host (git diff, hidden tests, judge).
    """

    def __init__(
        self,
        image: str = "python:3.12-slim",
        workdir_base: str = "./heval_workdir",
        gateway_host: str = "host.docker.internal",
        gateway_port: int = 8877,
        network: str = "heval-net",
        gateway_db: str = "heval_gateway.db",
        docker_bin: str = "docker",
        memory_limit: str | None = None,
        cpu_limit: str | None = None,
        use_host_network: bool = False,
    ) -> None:
        self.image = image
        self.workdir_base = Path(workdir_base)
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.network = network
        self.gateway_db = gateway_db
        self.docker_bin = docker_bin
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.use_host_network = use_host_network
        self.workdir_base.mkdir(parents=True, exist_ok=True)

    async def run_cell(self, cell: RunCell) -> dict[str, Any]:
        """Run a single eval cell in a Docker container.

        This is a high-level method that:
        1. Creates a workdir for the cell
        2. Clones and sets up the task repo (on the host)
        3. Runs the harness adapter inside a container
        4. Collects results and evaluates on the host

        Returns a dict with exit_class, success, usage, cost, etc.
        """
        from heval.evaluator.swe import SWEEvaluator
        from heval.gateway.models import TokenUsage
        from heval.gateway.store import CallStore

        cell_workdir = self.workdir_base / cell.cell_id
        cell_workdir.mkdir(parents=True, exist_ok=True)

        start_time = time.monotonic()

        try:
            # Clone and setup repo on the host (mounted into the container)
            if cell.task.repo_url:
                await self._clone_repo(cell, cell_workdir)
            else:
                # Create a minimal project structure for tasks without a repo
                (cell_workdir / "src").mkdir(exist_ok=True)
                (cell_workdir / "tests").mkdir(exist_ok=True)

            # Run the harness inside a Docker container
            harness_result = await self._run_harness(cell, cell_workdir)

            # Evaluate the result on the host
            if cell.task.track.value == "swe":
                evaluator = SWEEvaluator()
                repo_dir = (
                    cell_workdir / "repo"
                    if (cell_workdir / "repo").exists()
                    else cell_workdir
                )
                eval_result = evaluator.evaluate(cell.task, repo_dir)
            else:
                from heval.evaluator.open_ended import OpenEndedEvaluator

                oe_evaluator = OpenEndedEvaluator(
                    gateway_url=(
                        f"http://{self.gateway_host}:{self.gateway_port}"
                    )
                )
                oe_result = await oe_evaluator.evaluate(
                    cell.task, cell_workdir, trace_id=cell.cell_id
                )

                # Convert OpenEndedResult to the format expected by the runner
                from heval.evaluator.swe import ErrorClass, EvaluationResult

                # Map open-ended error classes to SWE ErrorClass
                error_class_map = {
                    "no_change": ErrorClass.NO_CHANGE,
                    "structural_failure": ErrorClass.CRASH,
                    "judge_error": ErrorClass.CRASH,
                    "success": ErrorClass.SUCCESS,
                    "partial": ErrorClass.PARTIAL,
                }
                mapped_error_class = error_class_map.get(
                    oe_result.error_class, ErrorClass.WRONG_APPROACH
                )

                eval_result = EvaluationResult(
                    exit_class=oe_result.exit_class,
                    success=oe_result.success,
                    error_class=mapped_error_class,
                    error_message=oe_result.judge_result.error or ""
                    if oe_result.judge_result
                    else "",
                    test_output=oe_result.test_output,
                    diff=oe_result.diff,
                )

            # Collect token usage from gateway (per-cell via trace_id)
            gateway_db_path = Path(self.gateway_db)
            usage = TokenUsage()
            total_cost = 0.0
            num_api_calls = 0
            if gateway_db_path.exists():
                store = CallStore(str(gateway_db_path))
                # Use trace_id to get only this cell's calls.
                calls = store.get_by_trace(cell.cell_id)
                if not calls:
                    logger.warning(
                        "No API calls found with trace_id=%s for cell %s; "
                        "cost attribution will be zero. This may indicate "
                        "trace ID propagation is not working.",
                        cell.cell_id,
                        cell.cell_id,
                    )
                for call in calls:
                    usage.input_tokens += call.usage.input_tokens
                    usage.output_tokens += call.usage.output_tokens
                    usage.cache_read_tokens += call.usage.cache_read_tokens
                    usage.cache_write_tokens += call.usage.cache_write_tokens
                    usage.reasoning_tokens += call.usage.reasoning_tokens
                    total_cost += call.cost.total
                    num_api_calls += 1

            latency_ms = (time.monotonic() - start_time) * 1000

            return {
                "exit_class": eval_result.exit_class,
                "success": eval_result.success,
                "error_class": eval_result.error_class.value,
                "error_message": eval_result.error_message,
                "usage": usage,
                "total_cost": total_cost,
                "latency_ms": latency_ms,
                "time_to_first_attempt_ms": harness_result.duration_ms,
                "num_api_calls": num_api_calls,
                "num_tool_calls": 0,
                "diff": eval_result.diff,
                "test_output": eval_result.test_output,
                "harness_metadata": {
                    "harness": cell.harness.name,
                    "model": cell.model.name,
                    "observability_tier": cell.harness.observability_tier,
                },
            }

        except subprocess.TimeoutExpired as e:
            raise RetryableError(f"Container timed out: {e}") from e
        except Exception as e:
            logger.error("Cell %s failed: %s", cell.cell_id, e)
            raise

    # ------------------------------------------------------------------
    # Host-side repo setup
    # ------------------------------------------------------------------

    async def _clone_repo(self, cell: RunCell, workdir: Path) -> None:
        """Clone or copy the task repo into the workdir on the host.

        Supports three cases:
        1. Remote URLs (``https://...``, ``git@...``, ``ssh://...``):
           ``git clone`` is used.
        2. Local paths that are git repos (have a ``.git`` dir):
           ``git clone`` is used to preserve history.
        3. Local paths that are plain directories (no ``.git``):
           ``shutil.copytree`` is used, then the copy is initialized as a
           fresh git repo with a single initial commit. This supports
           repos stored in the heval repo without embedded ``.git`` dirs.
        """
        import shutil

        if not cell.task.repo_url:
            return

        repo_url = cell.task.repo_url
        dest = workdir / "repo"

        # Remote URL → git clone
        if (
            repo_url.startswith("http://")
            or repo_url.startswith("https://")
            or repo_url.startswith("git@")
            or repo_url.startswith("ssh://")
        ):
            await self._git_clone(repo_url, dest)
            await self._git_checkout(dest, cell.task.repo_commit)
            return

        # Local path → resolve relative to project root
        local_path = Path(repo_url)
        if not local_path.is_absolute():
            local_path = Path(__file__).resolve().parents[3] / local_path
        if not local_path.exists():
            raise FileNotFoundError(
                f"Task repo not found: {repo_url} (resolved to "
                f"{local_path})"
            )

        if (local_path / ".git").exists():
            # Local git repo → clone to preserve history
            await self._git_clone(str(local_path), dest)
            await self._git_checkout(dest, cell.task.repo_commit)
        else:
            # Plain directory → copy and init a fresh git repo
            shutil.copytree(local_path, dest)
            await self._git_init_fresh(dest)

    async def _git_clone(self, src: str, dest: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", src, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Git clone failed: {stderr.decode()}")

    async def _git_checkout(self, dest: Path, commit: str | None) -> None:
        if not commit:
            return
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", commit,
            cwd=dest,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _git_init_fresh(self, dest: Path) -> None:
        """Init a fresh git repo and create an initial commit."""
        for args in (
            ["git", "init"],
            ["git", "config", "user.email", "heval@local"],
            ["git", "config", "user.name", "heval"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "Initial repo state"],
        ):
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=dest,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

    # ------------------------------------------------------------------
    # Docker container lifecycle
    # ------------------------------------------------------------------

    def _build_run_args(
        self,
        workdir: Path,
        env: dict[str, str],
        timeout: int,
        container_name: str,
    ) -> list[str]:
        """Build the ``docker run`` argument list for a detached container.

        The container runs a long-lived ``sleep`` so we can ``docker exec``
        into it for setup and harness execution.
        """
        args = [
            self.docker_bin,
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-v",
            f"{workdir.resolve()}:{CONTAINER_WORKSPACE}",
            "-w",
            CONTAINER_WORKSPACE,
        ]

        # Pass allowlisted env vars via --env (NOT the whole host env)
        for key, value in env.items():
            args.extend(["--env", f"{key}={value}"])

        # Gateway reachability: use host.docker.internal with --add-host,
        # or --network=host as a fallback.
        if self.use_host_network:
            args.extend(["--network", "host"])
        else:
            args.extend(
                ["--add-host", "host.docker.internal:host-gateway"]
            )

        # Resource limits
        if self.memory_limit:
            args.extend(["--memory", self.memory_limit])
        if self.cpu_limit:
            args.extend(["--cpus", self.cpu_limit])

        # Stop timeout so Docker kills the container promptly on stop
        args.extend(["--stop-timeout", str(timeout)])

        args.append(self.image)
        # Keep the container alive long enough for exec commands.
        args.extend(["sleep", str(timeout + 30)])
        return args

    async def _start_container(
        self, workdir: Path, env: dict[str, str], timeout: int, name: str
    ) -> str:
        """Launch a detached container and return its container ID.

        Raises ``RuntimeError`` if the container fails to start.
        """
        args = self._build_run_args(workdir, env, timeout, name)
        result = await _run_subprocess(args, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError("docker run produced no container ID")
        return container_id

    async def _exec_in_container(
        self,
        container_id: str,
        command: list[str],
        timeout: int,
        cwd: str | None = None,
    ) -> AdapterResult:
        """Run a command inside the container via ``docker exec``.

        Returns an :class:`AdapterResult` with captured stdout/stderr.

        Args:
            container_id: The container to exec in.
            command: The command list to run.
            timeout: Maximum execution time in seconds.
            cwd: Working directory inside the container. When provided,
                uses ``-w <cwd>`` instead of the default ``/workspace``.
        """
        workdir_flag = cwd if cwd is not None else CONTAINER_WORKSPACE
        exec_args = [
            self.docker_bin,
            "exec",
            "-w",
            workdir_flag,
            container_id,
            *command,
        ]
        start = time.monotonic()
        try:
            result = await _run_subprocess(exec_args, timeout=timeout)
            duration_ms = (time.monotonic() - start) * 1000
            return AdapterResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=False,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.monotonic() - start) * 1000
            return AdapterResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                timed_out=True,
                duration_ms=duration_ms,
            )

    async def _stop_container(self, container_id: str) -> None:
        """Stop and remove the container (best-effort; --rm handles removal)."""
        try:
            await _run_subprocess(
                [self.docker_bin, "stop", container_id], timeout=30
            )
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning("Failed to stop container %s: %s", container_id, e)

    async def _run_harness(self, cell: RunCell, workdir: Path) -> RunResult:
        """Run the harness adapter for this cell inside a Docker container.

        Uses the adapter registry to load the appropriate adapter based on
        ``cell.harness.adapter``, then executes the adapter's command inside
        the container via ``docker exec``.
        """
        from heval.adapters.registry import create_adapter

        start = time.monotonic()
        repo_dir = workdir / "repo" if (workdir / "repo").exists() else workdir
        timeout = cell.task.timeout_seconds

        # Create the adapter. The gateway URL uses the Docker-reachable host
        # (host.docker.internal), NOT 127.0.0.1 which is unreachable from
        # inside a container.
        gateway_url = f"http://{self.gateway_host}:{self.gateway_port}"
        adapter = create_adapter(
            name=cell.harness.adapter,
            workdir=str(workdir),
            model=cell.model,
            gateway_url=gateway_url,
            trace_id=cell.cell_id,
            config=cell.harness.config,
        )

        if adapter is None:
            # Fallback: no adapter found, write a placeholder
            (repo_dir / "HEVAL_OUTPUT.txt").write_text(
                f"No adapter found for: {cell.harness.adapter}\n"
            )
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=f"No adapter found for: {cell.harness.adapter}",
                timed_out=False,
                workdir=str(repo_dir),
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Get the allowlisted env vars (gateway URL, API key, trace_id, etc.)
        # These are passed to the container via --env, NOT the whole host env.
        env = adapter.get_env()

        # Write the setup script to the workdir so it is available inside
        # the container at /workspace/setup.sh (the workdir is mounted there).
        if cell.task.setup_script:
            setup_path = workdir / "setup.sh"
            setup_path.write_text(cell.task.setup_script)

        # Get the raw harness command to run inside the container.
        try:
            harness_cmd = adapter.get_command(cell.task.task_prompt)
        except NotImplementedError as e:
            logger.error("Adapter %s has no get_command: %s", cell.harness.adapter, e)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                timed_out=False,
                workdir=str(repo_dir),
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # NOTE: We intentionally skip adapter.prepare() here. In the Docker
        # runner, the harness binary lives inside the container, not on the
        # host, so host-side checks like shutil.which() would fail. The
        # adapter's run() method (used for local execution) still calls
        # prepare(). If the binary is missing inside the container, the
        # harness command itself will fail with a clear error.

        # Determine the working directory for exec commands inside the
        # container. When a repo is cloned, it lives at /workspace/repo;
        # otherwise use /workspace. Setup scripts (e.g. pip install -r
        # requirements.txt) and the harness must run inside the repo dir.
        exec_cwd = CONTAINER_REPO if cell.task.repo_url else CONTAINER_WORKSPACE

        # Launch the container
        container_name = f"heval-{cell.cell_id}"
        container_id: str | None = None
        try:
            container_id = await self._start_container(
                workdir, env, timeout, container_name
            )

            # Run setup script inside the container if present.
            # The script is at /workspace/setup.sh but executed with cwd
            # /workspace/repo so relative paths (e.g. requirements.txt)
            # resolve correctly.
            if cell.task.setup_script:
                setup_result = await self._exec_in_container(
                    container_id,
                    ["bash", "/workspace/setup.sh"],
                    timeout=timeout,
                    cwd=exec_cwd,
                )
                if setup_result.exit_code != 0:
                    logger.warning(
                        "Setup script failed for %s: %s",
                        cell.cell_id,
                        setup_result.stderr,
                    )

            # Run the harness command inside the container
            result = await self._exec_in_container(
                container_id, harness_cmd, timeout=timeout, cwd=exec_cwd
            )

        finally:
            if container_id is not None:
                await self._stop_container(container_id)
            try:
                await adapter.cleanup()
            except Exception as e:
                logger.warning("Adapter cleanup failed for %s: %s", cell.cell_id, e)

        # Ensure git tracks the changes on the host (for diff/evaluation)
        self._commit_changes(repo_dir)

        return RunResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            workdir=str(repo_dir),
            duration_ms=(time.monotonic() - start) * 1000,
        )

    def _commit_changes(self, repo_dir: Path) -> None:
        """Stage and commit harness changes on the host for diff evaluation."""
        if not (repo_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
            commit_msg = "initial"
        else:
            commit_msg = "harness output"

        # Always set local git identity before committing. This uses
        # local config (not --global) so it doesn't affect the host's
        # git config. Required because containers/CI may not have a
        # git identity configured.
        subprocess.run(
            ["git", "config", "user.email", "heval@local"],
            cwd=repo_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "heval"],
            cwd=repo_dir,
            capture_output=True,
        )
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True)
        commit_proc = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_dir,
            capture_output=True,
        )
        if commit_proc.returncode != 0:
            # No changes to commit is fine
            pass

    def cleanup(self, cell_workdir: Path) -> None:
        """Clean up the workdir for a cell."""
        if cell_workdir.exists():
            shutil.rmtree(cell_workdir)
