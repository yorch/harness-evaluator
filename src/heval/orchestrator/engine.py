"""Orchestrator: builds and executes the eval matrix.

Handles:
  - Matrix building (harness × model × task × repeat)
  - Budget caps (stops when $ budget exhausted)
  - Cell-level resumability (skips completed cells)
  - Retry logic for transient failures (RETRYABLE_KILL)
  - Progress tracking
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from heval.orchestrator.config import RunCell, RunConfig
from heval.orchestrator.results_store import ResultsStore

logger = logging.getLogger(__name__)


class CellStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExitClass(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    RETRYABLE_KILL = "retryable_kill"
    NON_RETRYABLE_KILL = "non_retryable_kill"


@dataclass
class OrchestratorProgress:
    """Tracks progress of an eval run."""

    total_cells: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    running: int = 0
    total_cost: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.completed + self.failed + self.skipped

    @property
    def progress_pct(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return self.done / self.total_cells * 100


class Orchestrator:
    """Executes the eval matrix with budget caps and resumability."""

    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 2.0  # seconds

    def __init__(
        self,
        config: RunConfig,
        results_store: ResultsStore,
        run_cell_fn: Any | None = None,
    ) -> None:
        """
        Args:
            config: The run configuration.
            results_store: Store for results.
            run_cell_fn: Async function(cell: RunCell) -> dict[str, Any].
                         If None, uses a dry-run placeholder.
        """
        self.config = config
        self.store = results_store
        self.run_cell_fn = run_cell_fn or _dry_run_cell
        self.progress = OrchestratorProgress()
        self._budget_lock = asyncio.Lock()
        # Remaining budget available for reservation (in-memory, guarded by
        # the budget lock). Initialized lazily on first use.
        self._remaining_budget: float | None = None
        # Tracks reserved amounts per cell so failures/cancellations can
        # release the reservation back to the remaining budget.
        self._reservations: dict[str, float] = {}
        # Total number of cells in the matrix, pre-computed in run() so
        # _estimate_cell_cost uses the real count (not len(tasks) which
        # is wrong when tasks=["*"]).
        self._total_cells: int | None = None

    async def run(self) -> OrchestratorProgress:
        """Execute the full eval matrix."""
        cells = self.config.build_matrix()
        self._total_cells = len(cells)
        self.progress.total_cells = len(cells)

        # Filter out already-completed cells (resumability)
        completed = self.store.get_completed_cells(self.config.name)
        pending_cells = [c for c in cells if c.cell_id not in completed]
        self.progress.skipped = len(cells) - len(pending_cells)
        logger.info(
            "Run '%s': %d total cells, %d already completed, %d to run",
            self.config.name,
            len(cells),
            self.progress.skipped,
            len(pending_cells),
        )

        # Execute cells (sequential or parallel)
        if self.config.parallel_runs <= 1:
            for cell in pending_cells:
                await self._run_cell_with_budget_check(cell)
        else:
            # Parallel execution with semaphore
            sem = asyncio.Semaphore(self.config.parallel_runs)
            tasks = [self._run_cell_with_budget_and_sem(sem, cell) for cell in pending_cells]
            await asyncio.gather(*tasks)

        logger.info(
            "Run '%s' complete: %d passed, %d failed, %d skipped, $%.4f spent",
            self.config.name,
            self.progress.completed,
            self.progress.failed,
            self.progress.skipped,
            self.progress.total_cost,
        )
        return self.progress

    async def _run_cell_with_budget_and_sem(
        self, sem: asyncio.Semaphore, cell: RunCell
    ) -> None:
        async with sem:
            await self._run_cell_with_budget_check(cell)

    async def _run_cell_with_budget_check(self, cell: RunCell) -> None:
        """Run a single cell with atomic budget reservation.

        Uses a reserve-and-reconcile pattern with the budget lock:
        1. RESERVE: under the lock, subtract the estimated cost from the
           remaining budget. If insufficient, skip the cell.
        2. Execute the cell outside the lock (allows parallelism).
        3. RECONCILE: under the lock, adjust the remaining budget based
           on the actual cost — refund the difference if the cell cost
           less than reserved, or deduct the shortfall if it cost more.
           Then save the result so the cost is recorded atomically.

        On failure or cancellation, the reservation is released back to
        the remaining budget.
        """
        # RESERVE: atomically subtract estimated cost from remaining budget
        if self.config.budget_usd is not None:
            async with self._budget_lock:
                if self._remaining_budget is None:
                    self._remaining_budget = self.config.budget_usd
                estimate = self._estimate_cell_cost(cell)
                if self._remaining_budget < estimate:
                    logger.warning(
                        "Budget cap reached ($%.4f remaining < $%.4f "
                        "estimated), skipping cell %s",
                        self._remaining_budget,
                        estimate,
                        cell.cell_id,
                    )
                    self.store.set_cell_state(
                        cell.cell_id, cell.run_name, "skipped",
                        "Budget cap reached",
                    )
                    self.progress.skipped += 1
                    return
                self._remaining_budget -= estimate
                self._reservations[cell.cell_id] = estimate

        self.store.set_cell_state(cell.cell_id, cell.run_name, "running")
        self.progress.running += 1

        # Run with retry logic
        retry_count = 0
        while retry_count <= self.MAX_RETRIES:
            try:
                result = await self.run_cell_fn(cell)
                exit_class = result.get("exit_class", ExitClass.FAIL.value)
                success = result.get("success", 0.0)
                cell_cost = result.get("total_cost", 0.0)

                # RECONCILE: adjust remaining budget and save under the lock
                async with self._budget_lock:
                    self._reconcile_reservation(cell.cell_id, cell_cost)

                    self.store.save_result(
                        cell=cell,
                        exit_class=exit_class,
                        success=success,
                        error_class=result.get("error_class"),
                        error_message=result.get("error_message"),
                        usage=result.get("usage"),
                        total_cost=cell_cost,
                        latency_ms=result.get("latency_ms", 0.0),
                        time_to_first_attempt_ms=result.get(
                            "time_to_first_attempt_ms", 0.0
                        ),
                        num_api_calls=result.get("num_api_calls", 0),
                        num_tool_calls=result.get("num_tool_calls", 0),
                        diff=result.get("diff"),
                        test_output=result.get("test_output"),
                        harness_metadata=result.get("harness_metadata"),
                        retry_count=retry_count,
                    )
                    self.store.set_cell_state(
                        cell.cell_id, cell.run_name, "completed"
                    )

                    # Post-update: warn if this cell pushed us over budget
                    if self.config.budget_usd is not None:
                        total = self.store.get_total_cost(self.config.name)
                        if total > self.config.budget_usd:
                            logger.warning(
                                "Budget exceeded after cell %s "
                                "($%.4f > $%.4f)",
                                cell.cell_id,
                                total,
                                self.config.budget_usd,
                            )

                self.progress.running -= 1
                self.progress.total_cost += cell_cost

                if exit_class == ExitClass.PASS.value:
                    self.progress.completed += 1
                else:
                    self.progress.failed += 1

                return

            except RetryableError as e:
                retry_count += 1
                if retry_count <= self.MAX_RETRIES:
                    delay = self.RETRY_BASE_DELAY * (2 ** (retry_count - 1))
                    logger.warning(
                        "Cell %s failed (retryable, attempt %d/%d): %s. "
                        "Retrying in %.1fs",
                        cell.cell_id,
                        retry_count,
                        self.MAX_RETRIES,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Cell %s exhausted retries (%d): %s",
                        cell.cell_id,
                        self.MAX_RETRIES,
                        e,
                    )
                    async with self._budget_lock:
                        self._release_reservation(cell.cell_id)
                        self.store.save_result(
                            cell=cell,
                            exit_class=ExitClass.RETRYABLE_KILL.value,
                            success=0.0,
                            error_class="retry_exhausted",
                            error_message=str(e),
                            retry_count=retry_count,
                        )
                        self.store.set_cell_state(
                            cell.cell_id, cell.run_name, "failed", str(e)
                        )
                    self.progress.running -= 1
                    self.progress.failed += 1
                    self.progress.errors.append(f"{cell.cell_id}: {e}")
                    return

            except Exception as e:
                logger.error(
                    "Cell %s failed (non-retryable): %s", cell.cell_id, e
                )
                async with self._budget_lock:
                    self._release_reservation(cell.cell_id)
                    self.store.save_result(
                        cell=cell,
                        exit_class=ExitClass.NON_RETRYABLE_KILL.value,
                        success=0.0,
                        error_class="non_retryable",
                        error_message=str(e),
                    )
                    self.store.set_cell_state(
                        cell.cell_id, cell.run_name, "failed", str(e)
                    )
                self.progress.running -= 1
                self.progress.failed += 1
                self.progress.errors.append(f"{cell.cell_id}: {e}")
                return

    def _estimate_cell_cost(self, cell: RunCell) -> float:
        """Estimate the cost of a cell for budget reservation.

        Uses ``cell.budget`` if explicitly set. Otherwise derives a
        reasonable estimate from the run-level budget divided equally
        across all matrix cells (using the real cell count from
        ``build_matrix()``, not ``len(config.tasks)`` which is wrong
        when tasks is ``["*"]``).
        """
        if cell.budget is not None:
            return cell.budget
        if self.config.budget_usd is not None:
            if self._total_cells is not None:
                total_cells = self._total_cells
            else:
                total_cells = len(self.config.build_matrix())
            return self.config.budget_usd / max(1, total_cells)
        return 0.0

    def _reconcile_reservation(
        self, cell_id: str, actual_cost: float
    ) -> None:
        """Reconcile a cell's reservation against its actual cost.

        If actual cost < reserved, refund the difference back to the
        remaining budget. If actual cost > reserved, deduct the
        shortfall. Clears the reservation entry afterwards.
        """
        reserved = self._reservations.pop(cell_id, 0.0)
        if self._remaining_budget is not None:
            difference = reserved - actual_cost
            if difference > 0:
                self._remaining_budget += difference
            elif difference < 0:
                self._remaining_budget += difference  # subtract shortfall

    def _release_reservation(self, cell_id: str) -> None:
        """Release a cell's reservation back to the remaining budget.

        Used when a cell fails or is cancelled before recording a cost.
        """
        reserved = self._reservations.pop(cell_id, 0.0)
        if self._remaining_budget is not None and reserved > 0:
            self._remaining_budget += reserved


class RetryableError(Exception):
    """Error that can be retried (rate limit, timeout, OOM)."""

    pass


async def _dry_run_cell(cell: RunCell) -> dict[str, Any]:
    """Placeholder cell runner for testing without real harnesses."""
    from heval.gateway.models import TokenUsage

    await asyncio.sleep(0.01)
    return {
        "exit_class": ExitClass.PASS.value,
        "success": 1.0,
        "usage": TokenUsage(input_tokens=100, output_tokens=50),
        "total_cost": 0.001,
        "latency_ms": 100.0,
        "num_api_calls": 1,
        "num_tool_calls": 0,
        "test_output": "dry run",
    }
