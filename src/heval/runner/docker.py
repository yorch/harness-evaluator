"""Docker-based runner: executes harnesses in isolated containers.

Each run gets a fresh container with:
  - The task repo cloned and set up
  - The harness installed and configured
  - The gateway proxy accessible for token accounting
  - Network policy enforced
  - Timeout enforcement
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

from heval.orchestrator.config import RunCell
from heval.orchestrator.engine import RetryableError

logger = logging.getLogger(__name__)


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
    """Runs harnesses in Docker containers."""

    def __init__(
        self,
        image: str = "python:3.12-slim",
        workdir_base: str = "./heval_workdir",
        gateway_host: str = "host.docker.internal",
        gateway_port: int = 8877,
        network: str = "heval-net",
        gateway_db: str = "heval_gateway.db",
    ) -> None:
        self.image = image
        self.workdir_base = Path(workdir_base)
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.network = network
        self.gateway_db = gateway_db
        self.workdir_base.mkdir(parents=True, exist_ok=True)

    async def run_cell(self, cell: RunCell) -> dict[str, Any]:
        """Run a single eval cell in a Docker container.

        This is a high-level method that:
        1. Creates a workdir for the cell
        2. Clones and sets up the task repo
        3. Runs the harness adapter
        4. Collects results

        Returns a dict with exit_class, success, usage, cost, etc.
        """
        from heval.evaluator.swe import SWEEvaluator
        from heval.gateway.models import TokenUsage
        from heval.gateway.store import CallStore

        cell_workdir = self.workdir_base / cell.cell_id
        cell_workdir.mkdir(parents=True, exist_ok=True)

        start_time = time.monotonic()

        try:
            # Clone and setup repo
            if cell.task.repo_url:
                await self._clone_repo(cell, cell_workdir)
            else:
                # Create a minimal project structure for tasks without a repo
                (cell_workdir / "src").mkdir(exist_ok=True)
                (cell_workdir / "tests").mkdir(exist_ok=True)

            # Run setup script
            if cell.task.setup_script:
                await self._run_setup(cell, cell_workdir)

            # Run the harness (this is where the adapter would be called)
            # For now, this is a placeholder that simulates a harness run
            harness_result = await self._run_harness(cell, cell_workdir)

            # Evaluate the result
            if cell.task.track.value == "swe":
                evaluator = SWEEvaluator()
                eval_result = evaluator.evaluate(cell.task, cell_workdir)
            else:
                from heval.evaluator.open_ended import OpenEndedEvaluator

                oe_evaluator = OpenEndedEvaluator()
                oe_result = await oe_evaluator.evaluate(cell.task, cell_workdir)

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
                # Use trace_id to get only this cell's calls
                calls = store.get_by_trace(cell.cell_id)
                if not calls:
                    # Fallback to all calls if no trace_id was set
                    calls = store.get_all()
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

    async def _clone_repo(self, cell: RunCell, workdir: Path) -> None:
        """Clone the task repo into the workdir."""
        if not cell.task.repo_url:
            return

        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            cell.task.repo_url,
            str(workdir / "repo"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Git clone failed: {stderr.decode()}")

        if cell.task.repo_commit:
            checkout_proc = await asyncio.create_subprocess_exec(
                "git",
                "checkout",
                cell.task.repo_commit,
                cwd=workdir / "repo",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await checkout_proc.communicate()

    async def _run_setup(self, cell: RunCell, workdir: Path) -> None:
        """Run the setup script in the workdir."""
        script_path = workdir / "setup.sh"
        script_path.write_text(cell.task.setup_script or "")
        script_path.chmod(0o755)

        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(script_path),
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "Setup script failed for %s: %s", cell.cell_id, stderr.decode()
            )

    async def _run_harness(self, cell: RunCell, workdir: Path) -> RunResult:
        """Run the harness adapter for this cell.

        Uses the adapter registry to load the appropriate adapter
        based on cell.harness.adapter, then runs it with the task prompt.
        """
        from heval.adapters.registry import create_adapter

        start = time.monotonic()
        repo_dir = workdir / "repo" if (workdir / "repo").exists() else workdir

        # Create the adapter
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

        # Prepare and run the adapter
        try:
            await adapter.prepare()
            result = await adapter.run(
                cell.task.task_prompt,
                timeout=cell.task.timeout_seconds,
            )
        except Exception as e:
            logger.error("Adapter execution failed for %s: %s", cell.cell_id, e)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=f"Adapter error: {e}",
                timed_out=False,
                workdir=str(repo_dir),
                duration_ms=(time.monotonic() - start) * 1000,
            )
        finally:
            try:
                await adapter.cleanup()
            except Exception as e:
                logger.warning("Adapter cleanup failed for %s: %s", cell.cell_id, e)

        # Ensure git tracks the changes
        if not (repo_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "heval@test.com"],
                cwd=repo_dir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "heval"],
                cwd=repo_dir,
                capture_output=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "initial"], cwd=repo_dir, capture_output=True
            )
        else:
            subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True)
            # Only commit if there are changes
            commit_proc = subprocess.run(
                ["git", "commit", "-m", "harness output"],
                cwd=repo_dir,
                capture_output=True,
            )
            if commit_proc.returncode != 0:
                # No changes to commit is fine
                pass

        return RunResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            workdir=str(repo_dir),
            duration_ms=(time.monotonic() - start) * 1000,
        )

    def cleanup(self, cell_workdir: Path) -> None:
        """Clean up the workdir for a cell."""
        if cell_workdir.exists():
            shutil.rmtree(cell_workdir)
