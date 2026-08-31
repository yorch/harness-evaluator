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
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_evaluator.adapters.base import AdapterResult
from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import (
    AuthMode,
    ModelRole,
    PhaseInput,
    PhaseSpec,
    RunCell,
    TaskTrack,
)
from harness_evaluator.orchestrator.engine import RetryableError

logger = logging.getLogger(__name__)

# Workspace path inside the container (workdir is mounted here).
CONTAINER_WORKSPACE = "/workspace"

# Repo subdirectory inside the container workspace.
CONTAINER_REPO = "/workspace/repo"

# Strict allow-list for container name characters (Docker requires
# [a-zA-Z0-9][a-zA-Z0-9_.-]*). We sanitize cell IDs to this charset.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_container_name(cell_id: str) -> str:
    """Sanitize a cell ID for use as a Docker container name.

    Docker container names must match ``[a-zA-Z0-9][a-zA-Z0-9_.-]*``.
    Cell IDs contain ``__`` separators which are fine, but any other
    unsafe character is replaced with ``-``.
    """
    name = _SAFE_NAME_RE.sub("-", cell_id)
    # Docker names must start with an alphanumeric character.
    if name and not name[0].isalnum():
        name = "harness-evaluator-" + name
    return f"harness-evaluator-{name}"


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
        workdir_base: str = "./harness_evaluator_workdir",
        gateway_host: str = "host.docker.internal",
        gateway_port: int = 8877,
        network: str = "harness-evaluator-net",
        gateway_db: str = "harness_evaluator_gateway.db",
        results_db: str = "harness_evaluator_results.db",
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
        self.results_db = results_db
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
        from harness_evaluator.evaluator.swe import SWEEvaluator
        from harness_evaluator.gateway.store import CallStore

        cell_workdir = (self.workdir_base / cell.cell_id).resolve()

        # Defense in depth: cell_id is built from validated harness/model/task
        # ids, but assert the resolved workdir stays under workdir_base before
        # we rmtree it, so a bug or unvalidated path can never delete host
        # files outside the workdir.
        workdir_base_resolved = self.workdir_base.resolve()
        if not cell_workdir.is_relative_to(workdir_base_resolved):
            raise ValueError(
                f"Cell workdir '{cell_workdir}' escapes workdir base "
                f"'{workdir_base_resolved}' (cell_id='{cell.cell_id}')."
            )

        # On re-runs (resumability), the workdir may contain stale state
        # from a previous attempt (partial git repo, dirty working tree,
        # prior harness output). Clean it to avoid git clone failures,
        # double-counted diffs, and hidden-test patch application errors.
        if cell_workdir.exists():
            shutil.rmtree(cell_workdir)
        cell_workdir.mkdir(parents=True, exist_ok=True)

        # Also delete any prior gateway calls for this trace_id so
        # token/cost aggregation does not double-count from a previous
        # attempt of the same cell.
        gateway_db_path = Path(self.gateway_db)
        if gateway_db_path.exists():
            store = CallStore(str(gateway_db_path))
            store.delete_by_trace(cell.cell_id)

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
            if cell.task.track == TaskTrack.MULTI_PHASE:
                harness_result, phase_results = await self._run_harness_multiphase(
                    cell, cell_workdir
                )
            else:
                harness_result = await self._run_harness(cell, cell_workdir)
                phase_results = None

            # Evaluate the result on the host
            if cell.task.track in (TaskTrack.SWE, TaskTrack.MULTI_PHASE):
                evaluator = SWEEvaluator()
                repo_dir = (
                    cell_workdir / "repo"
                    if (cell_workdir / "repo").exists()
                    else cell_workdir
                )
                # SWEEvaluator.evaluate uses synchronous subprocess calls
                # (git diff, git apply, pytest). Offload to a thread to
                # avoid blocking the event loop when parallel_runs > 1.
                eval_result = await asyncio.to_thread(
                    evaluator.evaluate, cell.task, repo_dir
                )
            else:
                from harness_evaluator.evaluator.open_ended import OpenEndedEvaluator

                oe_evaluator = OpenEndedEvaluator(
                    gateway_url=(
                        f"http://{self.gateway_host}:{self.gateway_port}"
                    )
                )
                oe_result = await oe_evaluator.evaluate(
                    cell.task, cell_workdir, trace_id=cell.cell_id
                )

                # Convert OpenEndedResult to the format expected by the runner
                from harness_evaluator.evaluator.swe import ErrorClass, EvaluationResult

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

                # For multi-phase tasks, aggregate across all phase trace IDs.
                # For single-phase, use the cell's trace_id directly.
                trace_ids: list[str] = [cell.cell_id]
                if phase_results:
                    trace_ids = [p["trace_id"] for p in phase_results]

                for tid in trace_ids:
                    calls = store.get_by_trace(tid)
                    for call in calls:
                        usage.input_tokens += call.usage.input_tokens
                        usage.output_tokens += call.usage.output_tokens
                        usage.cache_read_tokens += call.usage.cache_read_tokens
                        usage.cache_write_tokens += call.usage.cache_write_tokens
                        usage.reasoning_tokens += call.usage.reasoning_tokens
                        total_cost += call.cost.total
                        num_api_calls += 1

                # Enrich phase_results with per-phase cost data.
                if phase_results:
                    for phase in phase_results:
                        phase_calls = store.get_by_trace(phase["trace_id"])
                        phase_usage = TokenUsage()
                        phase_cost = 0.0
                        phase_api_calls = 0
                        for call in phase_calls:
                            phase_usage.input_tokens += call.usage.input_tokens
                            phase_usage.output_tokens += call.usage.output_tokens
                            phase_cost += call.cost.total
                            phase_api_calls += 1
                        # Store as plain dict for JSON serialization
                        # (harness_metadata is json.dumps'd by save_result).
                        phase["usage"] = {
                            "input_tokens": phase_usage.input_tokens,
                            "output_tokens": phase_usage.output_tokens,
                            "cache_read_tokens": phase_usage.cache_read_tokens,
                            "cache_write_tokens": phase_usage.cache_write_tokens,
                            "reasoning_tokens": phase_usage.reasoning_tokens,
                        }
                        phase["total_cost"] = phase_cost
                        phase["num_api_calls"] = phase_api_calls

                if num_api_calls == 0:
                    logger.warning(
                        "No API calls found for cell %s; "
                        "cost attribution will be zero. This may indicate "
                        "trace ID propagation is not working.",
                        cell.cell_id,
                    )

            # Reconcile gateway-captured usage against harness self-report.
            # Only attempt reconciliation when the gateway actually captured
            # calls — an all-zero proxy usage would produce spurious 100%
            # discrepancies. Reconciliation is observability and must never
            # abort a cell, so failures are logged and swallowed.
            reconciliation_summary: dict[str, Any] | None = None
            if num_api_calls > 0:
                try:
                    reconciliation_summary = self._reconcile_cell(
                        cell, harness_result, usage
                    )
                except Exception:
                    logger.exception(
                        "Reconciliation failed for cell %s", cell.cell_id
                    )

            latency_ms = (time.monotonic() - start_time) * 1000

            # Save per-phase results to the results store for multi-phase cells.
            if phase_results:
                from harness_evaluator.orchestrator.results_store import ResultsStore

                results_store = ResultsStore(self.results_db)
                results_store.save_phase_results(
                    cell_id=cell.cell_id,
                    run_name=cell.run_name,
                    phases=phase_results,
                )

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
                    "docker_image": cell.harness.resolve_image(self.image),
                    "phases": phase_results,
                    "review_model": (
                        cell.review_model.name if cell.review_model else None
                    ),
                    "reconciliation": reconciliation_summary,
                },
            }

        except subprocess.TimeoutExpired as e:
            raise RetryableError(f"Container timed out: {e}") from e
        except Exception as e:
            logger.error("Cell %s failed: %s", cell.cell_id, e)
            raise

    def _reconcile_cell(
        self,
        cell: RunCell,
        harness_result: RunResult,
        proxy_usage: TokenUsage,
    ) -> dict[str, Any] | None:
        """Reconcile gateway-captured usage against harness self-report.

        Creates the adapter (same config as the in-container run) to parse
        self-reported token usage from the harness stdout/stderr. When
        self-reported usage is available, calls :func:`reconcile_usage` and
        persists the result to the results store.

        Returns a summary dict (``matched``, ``max_discrepancy_pct``,
        ``details``) for inclusion in ``harness_metadata``, or ``None``
        when the harness does not report usage (reconciliation skipped).
        """
        from harness_evaluator.adapters.registry import create_adapter
        from harness_evaluator.gateway.reconcile import (
            ReconciliationStatus,
            reconcile_usage,
        )
        from harness_evaluator.orchestrator.results_store import ResultsStore

        gateway_url = f"http://{self.gateway_host}:{self.gateway_port}"
        adapter = create_adapter(
            name=cell.harness.adapter,
            workdir=str(self.workdir_base / cell.cell_id),
            model=cell.model,
            gateway_url=gateway_url,
            trace_id=cell.cell_id,
            config=cell.harness.config,
        )
        if adapter is None:
            return None

        self_reported = adapter.parse_self_reported_usage(
            harness_result.stdout, harness_result.stderr
        )
        if self_reported is None:
            return None

        result = reconcile_usage(
            proxy_usage=proxy_usage,
            self_report_usage=self_reported,
        )

        max_discrepancy = (
            max(result.discrepancies.values()) if result.discrepancies else 0.0
        )
        matched = result.status != ReconciliationStatus.DISCREPANCY
        summary: dict[str, Any] = {
            "matched": matched,
            "max_discrepancy_pct": max_discrepancy,
            "details": result.discrepancies,
        }

        results_store = ResultsStore(self.results_db)
        results_store.save_reconciliation_result(
            cell_id=cell.cell_id,
            run_name=cell.run_name,
            proxy_usage=result.proxy_usage,
            self_reported_usage=result.self_report_usage,
            matched=matched,
            max_discrepancy_pct=max_discrepancy,
            details=result.discrepancies,
        )
        return summary

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
           repos stored in the harness-evaluator repo without embedded ``.git`` dirs.
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
        project_root = Path(__file__).resolve().parents[3]
        local_path = Path(repo_url)
        if not local_path.is_absolute():
            local_path = (project_root / local_path).resolve()
            # Relative repo paths must stay within the project root — reject
            # traversal like ``../../sensitive`` before copying the tree.
            if not local_path.is_relative_to(project_root):
                raise ValueError(
                    f"Task repo_url '{repo_url}' escapes the project root."
                )
        else:
            local_path = local_path.resolve()
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
            # Plain directory → copy and init a fresh git repo.
            # shutil.copytree is blocking I/O — offload to a thread to
            # avoid stalling the asyncio event loop.
            await asyncio.to_thread(shutil.copytree, local_path, dest)
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
        # ``--`` terminates option parsing so ``commit`` can never be read as
        # a git flag (TaskSpec also validates it against a ref charset).
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", commit, "--",
            cwd=dest,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git checkout '{commit}' failed: {stderr.decode(errors='replace')}"
            )

    async def _git_init_fresh(self, dest: Path) -> None:
        """Init a fresh git repo and create an initial commit."""
        for args in (
            ["git", "init"],
            ["git", "config", "user.email", "harness-evaluator@local"],
            ["git", "config", "user.name", "harness-evaluator"],
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

    def _resolve_credential_mounts(
        self, auth_mode: AuthMode, credentials_path: str | None
    ) -> tuple[list[tuple[str, str]], dict[str, str], list[str]]:
        """Resolve Docker volume mounts and env vars for OAuth credentials.

        Returns a tuple of (volume_mounts, env_vars, git_exclude_paths).
        ``volume_mounts`` are (host_path, container_path) pairs for ``-v``
        flags. ``env_vars`` point the harness at the mounted credential
        directory. ``git_exclude_paths`` are workdir-relative names to keep
        out of the commit diff.

        The credential directory is copied to a temp directory on the host
        and mounted writable so the harness can refresh expired access
        tokens. The original credential files on the host are never
        modified or mounted directly.
        """
        if credentials_path is None:
            return [], {}, []

        cred_file = Path(credentials_path).expanduser().resolve()
        if not cred_file.exists():
            logger.warning(
                "Credential file not found, skipping mount: %s",
                credentials_path,
            )
            return [], {}, []

        cred_dir = cred_file.parent

        if auth_mode == AuthMode.CLAUDE_OAUTH:
            container_dir = f"{CONTAINER_WORKSPACE}/.claude"
            dest_name = ".claude"
        elif auth_mode == AuthMode.CODEX_CHATGPT:
            container_dir = f"{CONTAINER_WORKSPACE}/.codex"
            dest_name = ".codex"
        else:
            return [], {}, []

        tmp_dir = Path(tempfile.mkdtemp(prefix="harness-eval-cred-"))
        tmp_cred_dir = tmp_dir / dest_name
        shutil.copytree(
            cred_dir,
            tmp_cred_dir,
            dirs_exist_ok=True,
            ignore_dangling_symlinks=True,
        )

        env_key = (
            "CLAUDE_CONFIG_DIR"
            if auth_mode == AuthMode.CLAUDE_OAUTH
            else "CODEX_HOME"
        )
        return (
            [(str(tmp_cred_dir), container_dir)],
            {env_key: container_dir},
            [dest_name],
        )

    def _build_run_args(
        self,
        workdir: Path,
        env: dict[str, str],
        timeout: int,
        container_name: str,
        image: str | None = None,
        credential_mounts: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Build the ``docker run`` argument list for a detached container.

        The container runs a long-lived ``sleep`` so we can ``docker exec``
        into it for setup and harness execution.

        Security: containers are launched with ``--cap-drop=ALL`` to
        remove all Linux capabilities. The harness only needs to write
        files and make network requests to the gateway/provider — it
        does not need ``SYS_PTRACE``, ``NET_ADMIN``, etc.
        """
        args = [
            self.docker_bin,
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            # Drop all Linux capabilities for isolation. The harness
            # only needs file I/O and network access, not privileged
            # kernel operations.
            "--cap-drop=ALL",
            "-v",
            f"{workdir.resolve()}:{CONTAINER_WORKSPACE}",
        ]

        # Mount OAuth credential directories writable so subscription-
        # authenticated harnesses can refresh expired access tokens.
        # The mounted directory is a copy of the original (see
        # _resolve_credential_mounts), so the original is never modified.
        for host_path, container_path in credential_mounts or []:
            args.extend(["-v", f"{host_path}:{container_path}"])

        args.extend(["-w", CONTAINER_WORKSPACE])

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

        args.append(image or self.image)
        # Keep the container alive long enough for exec commands.
        args.extend(["sleep", str(timeout + 30)])
        return args

    async def _start_container(
        self,
        workdir: Path,
        env: dict[str, str],
        timeout: int,
        name: str,
        image: str | None = None,
        credential_mounts: list[tuple[str, str]] | None = None,
    ) -> str:
        """Launch a detached container and return its container ID.

        Raises ``RuntimeError`` if the container fails to start.
        """
        args = self._build_run_args(
            workdir, env, timeout, name, image, credential_mounts
        )
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
        env: dict[str, str] | None = None,
    ) -> AdapterResult:
        """Run a command inside the container via ``docker exec``.

        Returns an :class:`AdapterResult` with captured stdout/stderr.

        Args:
            container_id: The container to exec in.
            command: The command list to run.
            timeout: Maximum execution time in seconds.
            cwd: Working directory inside the container. When provided,
                uses ``-w <cwd>`` instead of the default ``/workspace``.
            env: Additional env vars to set via ``--env`` flags. Useful
                for multi-phase runs where the model changes between
                phases and the adapter env must be updated.
        """
        workdir_flag = cwd if cwd is not None else CONTAINER_WORKSPACE
        exec_args = [
            self.docker_bin,
            "exec",
            "-w",
            workdir_flag,
        ]
        if env:
            for key, val in env.items():
                exec_args.extend(["--env", f"{key}={val}"])
        exec_args.append(container_id)
        exec_args.extend(command)
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
        """Stop and remove the container (best-effort; --rm handles removal).

        Uses a short ``docker stop -t`` grace period and then force-removes
        the container. Relying on the container's own ``--stop-timeout`` (set
        to the task timeout, which may be many minutes) could otherwise let
        the CLI's own timeout fire first and leave the container running
        while evaluation begins.
        """
        try:
            # -t 5: give PID 1 five seconds to exit, then SIGKILL.
            await _run_subprocess(
                [self.docker_bin, "stop", "-t", "5", container_id], timeout=30
            )
        except Exception as e:
            logger.warning("Failed to stop container %s: %s", container_id, e)
            # Fall back to a forced removal so the container never lingers.
            try:
                await _run_subprocess(
                    [self.docker_bin, "rm", "-f", container_id], timeout=30
                )
            except Exception as e2:
                logger.warning("Failed to force-remove container %s: %s", container_id, e2)

    async def _run_harness(self, cell: RunCell, workdir: Path) -> RunResult:
        """Run the harness adapter for this cell inside a Docker container.

        Uses the adapter registry to load the appropriate adapter based on
        ``cell.harness.adapter``, then executes the adapter's command inside
        the container via ``docker exec``.
        """
        from harness_evaluator.adapters.registry import create_adapter

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

        # Allow pip install in task setup scripts despite PEP 668.
        # The container's Python is externally-managed (Debian), so task
        # setup scripts that run `pip install -r requirements.txt` would
        # fail without this. The Dockerfile also sets this as an ENV.
        env["PIP_BREAK_SYSTEM_PACKAGES"] = "1"

        # Resolve OAuth credential directory mounts for subscription auth.
        credential_mounts, cred_env, git_excludes = (
            self._resolve_credential_mounts(
                cell.model.auth_mode, cell.model.credentials_path
            )
        )
        env.update(cred_env)

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

        # Launch the container. The image is resolved per harness so a run can
        # mix harness versions (explicit HarnessSpec.docker_image, or a version
        # tag on the run-level image's repository); defaults to self.image.
        image = cell.harness.resolve_image(self.image)
        container_name = _sanitize_container_name(cell.cell_id)
        container_id: str | None = None
        try:
            container_id = await self._start_container(
                workdir, env, timeout, container_name, image,
                credential_mounts,
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
        await self._commit_changes(repo_dir, git_excludes)

        # Surface harness timeout as a retryable error so the orchestrator
        # can retry with backoff instead of scoring it as NO_CHANGE or
        # WRONG_APPROACH.
        if result.timed_out:
            raise RetryableError(
                f"Harness command timed out after {cell.task.timeout_seconds}s"
            )

        return RunResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            workdir=str(repo_dir),
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def _run_harness_multiphase(
        self, cell: RunCell, workdir: Path
    ) -> tuple[RunResult, list[dict[str, Any]]]:
        """Run a multi-phase task: sequential harness invocations in one container.

        Each phase runs inside the same Docker container so repo state
        persists between phases. Implementation phases use ``cell.model``,
        review phases use ``cell.review_model`` (if set). Phase inputs
        (diffs, feedback) are passed forward by injecting them into the
        next phase's prompt.

        Returns the final phase's RunResult plus a list of per-phase
        metadata dicts (trace_id, model, duration, exit_code).
        """
        from harness_evaluator.adapters.registry import create_adapter

        start = time.monotonic()
        repo_dir = workdir / "repo" if (workdir / "repo").exists() else workdir
        gateway_url = f"http://{self.gateway_host}:{self.gateway_port}"
        image = cell.harness.resolve_image(self.image)
        container_name = _sanitize_container_name(cell.cell_id)
        container_id: str | None = None
        phase_results: list[dict[str, Any]] = []
        last_result: AdapterResult | None = None

        # Track outputs from prior phases for input injection.
        prior_diff: str | None = None
        prior_output: str | None = None
        review_feedback: str | None = None
        # Union of git exclude paths across all phases (for final commit).
        all_git_excludes: list[str] = []
        # Union of credential mounts across all phases (for container start).
        all_credential_mounts: list[tuple[str, str]] = []

        # Precompute credential mounts for all phase models so the container
        # has all needed credential directories from the start.
        for phase in cell.task.phases:
            if phase.model_role == ModelRole.REVIEW and cell.review_model:
                phase_model = cell.review_model
            else:
                phase_model = cell.model
            phase_creds, _, phase_git_excl = (
                self._resolve_credential_mounts(
                    phase_model.auth_mode, phase_model.credentials_path
                )
            )
            for mount in phase_creds:
                if mount not in all_credential_mounts:
                    all_credential_mounts.append(mount)
            all_git_excludes.extend(phase_git_excl)

        try:
            for _idx, phase in enumerate(cell.task.phases):
                # Select the model for this phase based on its role.
                if phase.model_role == ModelRole.REVIEW and cell.review_model:
                    phase_model = cell.review_model
                else:
                    phase_model = cell.model

                # Per-phase trace ID so gateway costs are attributable per phase.
                phase_trace_id = f"{cell.cell_id}__phase-{phase.name}"

                # Delete prior gateway calls for this phase trace (resumability).
                gateway_db_path = Path(self.gateway_db)
                if gateway_db_path.exists():
                    from harness_evaluator.gateway.store import CallStore

                    store = CallStore(str(gateway_db_path))
                    store.delete_by_trace(phase_trace_id)

                adapter = create_adapter(
                    name=cell.harness.adapter,
                    workdir=str(workdir),
                    model=phase_model,
                    gateway_url=gateway_url,
                    trace_id=phase_trace_id,
                    config=cell.harness.config,
                )

                if adapter is None:
                    last_result = AdapterResult(
                        exit_code=-1,
                        stdout="",
                        stderr=f"No adapter for: {cell.harness.adapter}",
                        timed_out=False,
                        duration_ms=0.0,
                    )
                    phase_results.append(
                        {
                            "name": phase.name,
                            "trace_id": phase_trace_id,
                            "model": phase_model.name,
                            "exit_code": -1,
                            "duration_ms": 0.0,
                            "error": "no adapter",
                        }
                    )
                    break

                env = adapter.get_env()
                _, cred_env, git_excludes = (
                    self._resolve_credential_mounts(
                        phase_model.auth_mode, phase_model.credentials_path
                    )
                )
                env.update(cred_env)

                # Build the phase prompt, injecting prior phase output if needed.
                phase_prompt = self._build_phase_prompt(
                    phase, prior_diff, prior_output, review_feedback
                )

                harness_cmd = adapter.get_command(phase_prompt)
                exec_cwd = (
                    CONTAINER_REPO if cell.task.repo_url else CONTAINER_WORKSPACE
                )

                # Start container on first phase; reuse for subsequent phases.
                if container_id is None:
                    # Use the max phase timeout so the container lives long
                    # enough for all phases, not just the first one.
                    container_timeout = max(
                        p.timeout_seconds for p in cell.task.phases
                    )
                    # Start with a minimal base env (no API keys) — per-phase
                    # secrets are passed via docker exec --env on each phase
                    # to avoid leaking phase 1's keys into phase 2.
                    base_env = {
                        k: v for k, v in env.items()
                        if k in ("PATH", "HOME", "USER", "SHELL", "LANG",
                                 "LC_ALL", "TERM", "TMPDIR",
                                 "PIP_BREAK_SYSTEM_PACKAGES")
                    }
                    container_id = await self._start_container(
                        workdir,
                        base_env,
                        container_timeout,
                        container_name,
                        image,
                        all_credential_mounts,
                    )
                    # Run setup script if present (only on first phase).
                    if cell.task.setup_script:
                        setup_path = workdir / "setup.sh"
                        setup_path.write_text(cell.task.setup_script)
                        setup_result = await self._exec_in_container(
                            container_id,
                            ["bash", "/workspace/setup.sh"],
                            timeout=phase.timeout_seconds,
                            cwd=exec_cwd,
                        )
                        if setup_result.exit_code != 0:
                            raise RuntimeError(
                                f"Setup script failed for {cell.cell_id}: "
                                f"{setup_result.stderr.strip()}"
                            )

                # Run the harness command for this phase.
                # Always pass the full per-phase env via --env flags so
                # each phase gets its own API key and base URL.
                result = await self._exec_in_container(
                    container_id, harness_cmd, timeout=phase.timeout_seconds,
                    cwd=exec_cwd, env=env,
                )
                last_result = result

                phase_results.append(
                    {
                        "name": phase.name,
                        "trace_id": phase_trace_id,
                        "model": phase_model.name,
                        "model_role": phase.model_role.value,
                        "exit_code": result.exit_code,
                        "duration_ms": result.duration_ms,
                        "timed_out": result.timed_out,
                    }
                )

                if result.timed_out:
                    raise RetryableError(
                        f"Phase '{phase.name}' timed out after "
                        f"{phase.timeout_seconds}s"
                    )

                # Capture phase outputs for the next phase's input.
                if phase.model_role == ModelRole.REVIEW:
                    review_feedback = result.stdout + result.stderr
                else:
                    # Implementation phase: capture the diff BEFORE committing
                    # so the review phase sees the actual changes. Using
                    # get_workdir_diff handles committed, staged, and
                    # untracked file states.
                    from harness_evaluator.evaluator.utils import get_workdir_diff

                    prior_diff = await asyncio.to_thread(
                        get_workdir_diff, repo_dir
                    )
                    prior_output = result.stdout + result.stderr
                    # Commit after capturing the diff.
                    await self._commit_changes(repo_dir, git_excludes)

                await adapter.cleanup()

                # If a phase fails (non-zero exit), stop the pipeline.
                # Don't commit partial/failed changes.
                if result.exit_code != 0:
                    logger.warning(
                        "Phase '%s' failed (exit %d) for cell %s; "
                        "stopping multi-phase pipeline",
                        phase.name,
                        result.exit_code,
                        cell.cell_id,
                    )
                    break

        finally:
            if container_id is not None:
                await self._stop_container(container_id)

        # Final commit to capture all changes for evaluation.
        # Use the union of all phases' git excludes to avoid committing
        # OAuth credential files.
        await self._commit_changes(repo_dir, all_git_excludes or None)

        if last_result is None:
            last_result = AdapterResult(
                exit_code=-1,
                stdout="",
                stderr="No phases executed",
                timed_out=False,
                duration_ms=0.0,
            )

        run_result = RunResult(
            exit_code=last_result.exit_code,
            stdout=last_result.stdout,
            stderr=last_result.stderr,
            timed_out=last_result.timed_out,
            workdir=str(repo_dir),
            duration_ms=(time.monotonic() - start) * 1000,
        )
        return run_result, phase_results

    def _build_phase_prompt(
        self,
        phase: PhaseSpec,
        prior_diff: str | None,
        prior_output: str | None,
        review_feedback: str | None,
    ) -> str:
        """Build the prompt for a phase, injecting prior phase output.

        The injected content is appended to the phase's base prompt in a
        clearly delimited section so the harness knows what it's reviewing.
        """
        prompt = phase.task_prompt

        if phase.input == PhaseInput.DIFF and prior_diff:
            prompt += (
                "\n\n---\nHere is the diff from the implementation phase. "
                "Review it for correctness, security, and edge cases:\n\n"
                f"```diff\n{prior_diff}\n```"
            )
        elif phase.input == PhaseInput.OUTPUT and prior_output:
            prompt += (
                "\n\n---\nHere is the output from the prior phase:\n\n"
                f"```\n{prior_output}\n```"
            )
        elif phase.input == PhaseInput.REVIEW_FEEDBACK and review_feedback:
            prompt += (
                "\n\n---\nHere is feedback from an adversarial reviewer. "
                "Address each issue before submitting:\n\n"
                f"{review_feedback}"
            )

        return prompt

    async def _commit_changes(
        self, repo_dir: Path, exclude_paths: list[str] | None = None
    ) -> None:
        """Stage and commit harness changes on the host for diff evaluation.

        Uses ``asyncio.to_thread`` to avoid blocking the event loop with
        synchronous ``subprocess.run`` calls.
        """
        await asyncio.to_thread(
            self._commit_changes_sync, repo_dir, exclude_paths
        )

    def _commit_changes_sync(
        self, repo_dir: Path, exclude_paths: list[str] | None = None
    ) -> None:
        """Synchronous implementation of _commit_changes."""
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
            ["git", "config", "user.email", "harness-evaluator@local"],
            cwd=repo_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "harness-evaluator"],
            cwd=repo_dir,
            capture_output=True,
        )
        # Exclude OAuth credential mount points from the commit so
        # tokens are never captured in the diff (defense in depth).
        add_args: list[str] = ["git", "add", "-A"]
        if exclude_paths:
            add_args.append("--")
            add_args.append(".")
            add_args.extend(f":(exclude){p}" for p in exclude_paths)
            add_args.extend(f":(exclude)**/{p}" for p in exclude_paths)
        subprocess.run(add_args, cwd=repo_dir, capture_output=True)
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
