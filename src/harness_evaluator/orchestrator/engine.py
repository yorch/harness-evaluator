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
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from harness_evaluator.orchestrator.config import CostMode, RunCell, RunConfig, TaskTrack
from harness_evaluator.orchestrator.results_store import ResultsStore

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
    """Tracks progress of an eval run.

    ``current_cell`` holds the cell ID of the most recently started cell.
    For sequential runs this is the single in-flight cell; for parallel runs
    it is the last cell to transition to ``running`` (a best-effort hint for
    live progress display, not an exhaustive list of running cells).

    ``running_cells`` is the full list of currently in-flight cell IDs,
    suitable for showing all active cells in the TUI footer.

    ``total_cost`` is the informational "what this would have cost" figure:
    it accumulates the true cost of every cell, including budget-exempt
    cells whose spend is covered by the harness's own subscription. This is
    what the CLI prints as "Cost:". It is distinct from the *budget*
    (``Orchestrator._remaining_budget`` and the reservation/reconciliation
    arithmetic that guards it), which excludes budget-exempt cells — a
    broader set than "subscription-mode", since a mixed cell with a
    platform ``review_model`` or an open-ended judge is NOT exempt even
    when ``model.cost_mode`` is subscription — see
    ``Orchestrator._is_budget_exempt``, ``_reconcile_reservation`` and
    ``_estimate_cell_cost``.
    """

    total_cells: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    running: int = 0
    total_cost: float = 0.0
    errors: list[str] = field(default_factory=list)
    current_cell: str | None = None
    running_cells: list[str] = field(default_factory=list)
    skip_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def done(self) -> int:
        return self.completed + self.failed + self.skipped

    @property
    def progress_pct(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return self.done / self.total_cells * 100

    def snapshot(self) -> OrchestratorProgress:
        """Return a shallow copy safe for reading outside the progress lock.

        ``errors`` is copied so the clone is independent; the other fields are
        immutable scalars or ``str | None``.
        """
        return OrchestratorProgress(
            total_cells=self.total_cells,
            completed=self.completed,
            failed=self.failed,
            skipped=self.skipped,
            running=self.running,
            total_cost=self.total_cost,
            errors=list(self.errors),
            current_cell=self.current_cell,
            running_cells=list(self.running_cells),
            skip_reasons=dict(self.skip_reasons),
        )


class Orchestrator:
    """Executes the eval matrix with budget caps and resumability."""

    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 2.0  # seconds

    def __init__(
        self,
        config: RunConfig,
        results_store: ResultsStore,
        run_cell_fn: Any | None = None,
        on_progress: Callable[[OrchestratorProgress], None] | None = None,
    ) -> None:
        """
        Args:
            config: The run configuration.
            results_store: Store for results.
            run_cell_fn: Async function(cell: RunCell) -> dict[str, Any].
                         If None, uses a dry-run placeholder.
            on_progress: Optional callback invoked with a progress snapshot
                         after every progress mutation (cell start/complete/
                         fail/skip/retry). The snapshot is built under the
                         progress lock; the callback itself is invoked outside
                         the lock so a slow UI cannot block cell execution.
                         Defaults to None (no notifications).
        """
        self.config = config
        self.store = results_store
        self.run_cell_fn = run_cell_fn or _dry_run_cell
        self.progress = OrchestratorProgress()
        self._on_progress = on_progress
        self._budget_lock = asyncio.Lock()
        # Lock for progress counter mutations so concurrent cells do
        # not lose updates to running/completed/failed/total_cost.
        self._progress_lock = asyncio.Lock()
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
        """Execute the full eval matrix.

        Resume asymmetry (F12, documentation only — not a bug): a cell can
        finish a run in one of three states, and only one of them is skipped
        on resume:

        - ``completed`` — the harness ran to completion, whether it passed
          or failed the task (``exit_class`` is ``"pass"`` *or* ``"fail"``;
          see ``_run_cell_with_budget_check``). Filtered out of
          ``pending_cells`` by ``get_completed_cells`` and reported as
          "already completed (resumability)". This is the *only* state
          skipped on resume.
        - ``failed`` — an infrastructural failure (retries exhausted, or an
          unexpected exception from ``run_cell_fn``; see ``_save_failure``).
          Not filtered by ``get_completed_cells``, so it *is* re-run on
          resume, with no skip reason recorded (it simply runs again).
        - ``skipped`` — the budget cap was reached before the cell ran (see
          the reservation check below). Also not filtered by
          ``get_completed_cells``, so it too *is* re-run on resume (and may
          succeed if more budget or a higher cap is available this time).

        This is deliberate — an infra failure may be transient and worth
        retrying, a budget skip may become affordable on a later resume, and
        a harness that genuinely attempted and failed the task already
        produced a real result that resuming would only duplicate — but only
        ``completed`` is filtered by ``get_completed_cells``, so it is worth
        calling out explicitly here.
        """
        cells = self.config.build_matrix()
        self._total_cells = len(cells)
        self.progress.total_cells = len(cells)

        # Save run metadata for reproducibility.
        try:
            import harness_evaluator

            harness_evaluator_version = getattr(harness_evaluator, "__version__", None)
        except Exception:
            harness_evaluator_version = None
        self.store.save_run_metadata(
            run_name=self.config.name,
            config_json=self.config.model_dump_json(indent=2),
            harness_evaluator_version=harness_evaluator_version,
            docker_image=self.config.docker_image,
        )

        # Seed progress.total_cost from the store on resume regardless of
        # whether a budget is configured (F8) — this is the informational
        # "what this would have cost" figure (get_total_cost), so it always
        # includes subscription-mode spend even though that spend never
        # counted against the dollar budget.
        self.progress.total_cost = self.store.get_total_cost(self.config.name)

        # Initialize the remaining budget from the cap minus any *billable*
        # cost already recorded for this run (get_billable_cost excludes
        # budget-exempt cells — see docstring on _reconcile_reservation).
        # Without this, a resumed run would let pending cells spend the full
        # budget again on top of prior spend. Deliberately NOT floored at
        # 0.0: overspend debt from a prior run/process must survive a
        # restart exactly as it survives within a run (see
        # _reconcile_reservation) — flooring here would re-open a cap that
        # was already blown on every resume, which is unbounded leakage for
        # budget_usd == 0.0 (one more non-exempt cell billed per resume).
        if self.config.budget_usd is not None:
            already_spent = self.store.get_billable_cost(self.config.name)
            self._remaining_budget = self.config.budget_usd - already_spent
            logger.info(
                "Run '%s': budget $%.4f, already spent (billable) $%.4f, $%.4f remaining",
                self.config.name,
                self.config.budget_usd,
                already_spent,
                self._remaining_budget,
            )

        # Filter out already-completed cells (resumability)
        completed = self.store.get_completed_cells(self.config.name)
        pending_cells = [c for c in cells if c.cell_id not in completed]
        self.progress.skipped = len(cells) - len(pending_cells)
        for c in cells:
            if c.cell_id in completed:
                self.progress.skip_reasons[c.cell_id] = "already completed (resumability)"
        await self._notify_progress()
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
            # Parallel execution with semaphore. return_exceptions=True so a
            # single cell raising an unexpected error does not cancel its
            # siblings mid-flight (each cell already records its own outcome).
            sem = asyncio.Semaphore(self.config.parallel_runs)
            tasks = [self._run_cell_with_budget_and_sem(sem, cell) for cell in pending_cells]
            gather_results = await asyncio.gather(*tasks, return_exceptions=True)
            for cell, outcome in zip(pending_cells, gather_results, strict=True):
                if isinstance(outcome, BaseException):
                    logger.error("Cell %s raised unexpectedly: %s", cell.cell_id, outcome)

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

        On success, the cell's state is set to ``completed`` regardless of
        whether ``exit_class`` is ``"pass"`` or ``"fail"`` — the harness ran
        and produced a real result, so the cell is skipped on resume. This
        differs from an infrastructural failure (see ``_save_failure``),
        which is recorded as ``failed`` and so is re-run on resume, and from
        a budget skip (below), which is also re-run on resume (F12; see the
        full state breakdown in ``run()``'s docstring).
        """
        # RESERVE: atomically subtract estimated cost from remaining budget
        if self.config.budget_usd is not None:
            async with self._budget_lock:
                if self._remaining_budget is None:
                    self._remaining_budget = self.config.budget_usd
                estimate = self._estimate_cell_cost(cell)
                # Gate on exemption, not on the numeric estimate: a
                # budget-exempt cell (see _is_budget_exempt) can never be
                # unaffordable, regardless of what _remaining_budget
                # currently holds. Testing `estimate > 0` instead would
                # make a valid `budget_usd: 0.0` config (no `gt`/`ge`
                # constraint on RunConfig.budget_usd) an inert cap, since
                # EVERY cell's per-cell estimate is then 0.0 too — and with
                # a zero estimate, `_remaining_budget -= estimate` reserves
                # nothing, so under parallel_runs > 1 every in-flight cell
                # would be admitted before any of them reconciles. The
                # explicit `remaining <= 0` half denies a non-exempt cell
                # outright once there is no budget left at all (including
                # exactly $0.00), independent of what its own zero estimate
                # would otherwise suggest.
                if not self._is_budget_exempt(cell) and (
                    self._remaining_budget <= 0 or self._remaining_budget < estimate
                ):
                    logger.warning(
                        "Budget cap reached ($%.4f remaining < $%.4f "
                        "estimated), skipping cell %s",
                        self._remaining_budget,
                        estimate,
                        cell.cell_id,
                    )
                    skip_reason = (
                        f"Budget cap reached (${self._remaining_budget:.4f} "
                        f"remaining < ${estimate:.4f} estimated)"
                    )
                    self.store.set_cell_state(
                        cell.cell_id, cell.run_name, "skipped",
                        skip_reason,
                    )
                    async with self._progress_lock:
                        self.progress.skipped += 1
                        self.progress.skip_reasons[cell.cell_id] = skip_reason
                    await self._notify_progress()
                    return
                self._remaining_budget -= estimate
                self._reservations[cell.cell_id] = estimate

        self.store.set_cell_state(cell.cell_id, cell.run_name, "running")
        async with self._progress_lock:
            self.progress.running += 1
            self.progress.current_cell = cell.cell_id
            self.progress.running_cells.append(cell.cell_id)
        await self._notify_progress()

        try:
            # --- Phase 1: run the harness with retry (only run_cell_fn is
            # retried; persistence is handled separately below so a store
            # error can never masquerade as a harness failure). ---
            result: dict[str, Any] | None = None
            retry_count = 0
            while True:
                try:
                    result = await self.run_cell_fn(cell)
                    break
                except RetryableError as e:
                    if retry_count < self.MAX_RETRIES:
                        retry_count += 1
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
                        continue
                    logger.error(
                        "Cell %s exhausted retries (%d): %s",
                        cell.cell_id,
                        self.MAX_RETRIES,
                        e,
                    )
                    async with self._budget_lock:
                        self._release_reservation(cell.cell_id)
                        self._save_failure(
                            cell, ExitClass.RETRYABLE_KILL.value,
                            "retry_exhausted", str(e), retry_count,
                        )
                    async with self._progress_lock:
                        self.progress.failed += 1
                        self.progress.errors.append(f"{cell.cell_id}: {e}")
                    await self._notify_progress()
                    return

            # --- Phase 2: persist the harness result. The harness completed,
            # so persistence errors are logged but never reclassified as a
            # harness failure (which would lose the real result). ---
            exit_class = result.get("exit_class", ExitClass.FAIL.value)
            success = result.get("success", 0.0)
            cell_cost = result.get("total_cost", 0.0)
            try:
                async with self._budget_lock:
                    self._reconcile_reservation(cell, cell_cost)
                    self.store.save_result(
                        cell=self._billing_cell(cell),
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
                        harness_stdout=result.get("harness_stdout"),
                        harness_stderr=result.get("harness_stderr"),
                        retry_count=retry_count,
                    )
                    self.store.set_cell_state(cell.cell_id, cell.run_name, "completed")
                    if self.config.budget_usd is not None:
                        # By this point the result and "completed" state
                        # are already durably persisted, so a failure of
                        # this read alone (e.g. `database is locked` under
                        # parallel_runs > 1 — exactly the scenario that
                        # motivates budgeted runs) must not cause the run
                        # to under-report a cell it actually finished; skip
                        # only the warning, not the counter update below.
                        try:
                            billable_total: float | None = self.store.get_billable_cost(
                                self.config.name
                            )
                        except Exception as billable_err:
                            logger.error(
                                "Failed to re-derive billable cost for run "
                                "'%s' after cell %s (result already "
                                "persisted): %s",
                                self.config.name,
                                cell.cell_id,
                                billable_err,
                            )
                            billable_total = None
                        if billable_total is not None and billable_total > self.config.budget_usd:
                            logger.warning(
                                "Budget exceeded after cell %s ($%.4f > $%.4f)",
                                cell.cell_id,
                                billable_total,
                                self.config.budget_usd,
                            )
                    # Re-derive from the store (rather than `+= cell_cost`)
                    # so progress.total_cost can never drift from what was
                    # actually persisted — e.g. if save_result committed but
                    # set_cell_state then raised on a prior attempt, leaving
                    # a completed row whose cost an incremental `+=` would
                    # double-count on the resume that re-runs this cell.
                    # By this point the result and "completed" state are
                    # already durably persisted, so a failure of this read
                    # alone (e.g. `database is locked` under
                    # parallel_runs > 1) must not cause the run to
                    # under-report a cell it actually finished — fall back
                    # to the old incremental update instead of losing the
                    # completion signal below.
                    try:
                        total_cost_now: float | None = self.store.get_total_cost(
                            self.config.name
                        )
                    except Exception as total_cost_err:
                        logger.error(
                            "Failed to re-derive total_cost for run '%s' after "
                            "cell %s (result already persisted): %s",
                            self.config.name,
                            cell.cell_id,
                            total_cost_err,
                        )
                        total_cost_now = None
                async with self._progress_lock:
                    if total_cost_now is not None:
                        self.progress.total_cost = total_cost_now
                    else:
                        self.progress.total_cost += cell_cost
                    if exit_class == ExitClass.PASS.value:
                        self.progress.completed += 1
                    else:
                        self.progress.failed += 1
                        # Surface eval failures (not just infra kills) in
                        # progress.errors so the CLI summary can show
                        # *why* a cell failed, not just that it did.
                        err_cls = result.get("error_class") or "unknown"
                        err_msg = result.get("error_message") or ""
                        if err_msg:
                            self.progress.errors.append(
                                f"{cell.cell_id}: {err_cls} — {err_msg}"
                            )
                        else:
                            self.progress.errors.append(
                                f"{cell.cell_id}: {err_cls}"
                            )
                await self._notify_progress()
            except Exception as persist_err:
                logger.error(
                    "Failed to persist result for cell %s (harness completed): %s",
                    cell.cell_id,
                    persist_err,
                )
            return

        except asyncio.CancelledError:
            logger.warning("Cell %s cancelled", cell.cell_id)
            raise
        except Exception as e:
            # Non-retryable error raised by the harness (run_cell_fn).
            logger.error("Cell %s failed (non-retryable): %s", cell.cell_id, e)
            async with self._budget_lock:
                self._release_reservation(cell.cell_id)
                self._save_failure(
                    cell, ExitClass.NON_RETRYABLE_KILL.value,
                    "non_retryable", str(e), 0,
                )
            async with self._progress_lock:
                self.progress.failed += 1
                self.progress.errors.append(f"{cell.cell_id}: {e}")
            await self._notify_progress()
            return
        finally:
            # Guarantee cleanup on every path (including cancellation):
            # release any dangling reservation (idempotent — a no-op once
            # reconciled/released) and decrement the running counter once.
            async with self._budget_lock:
                self._release_reservation(cell.cell_id)
            async with self._progress_lock:
                self.progress.running -= 1
                if cell.cell_id in self.progress.running_cells:
                    self.progress.running_cells.remove(cell.cell_id)
            await self._notify_progress()

    def _save_failure(
        self,
        cell: RunCell,
        exit_class: str,
        error_class: str,
        error_message: str,
        retry_count: int,
    ) -> None:
        """Persist a failed cell result, swallowing store errors.

        A persistence failure here must not propagate and abort the run.
        """
        try:
            self.store.save_result(
                cell=self._billing_cell(cell),
                exit_class=exit_class,
                success=0.0,
                error_class=error_class,
                error_message=error_message,
                retry_count=retry_count,
            )
            self.store.set_cell_state(cell.cell_id, cell.run_name, "failed", error_message)
        except Exception as persist_err:
            logger.error(
                "Failed to persist failure for cell %s: %s", cell.cell_id, persist_err
            )

    def _is_budget_exempt(self, cell: RunCell) -> bool:
        """Return whether NO part of this cell's trace can bill real dollars.

        A cell's ``total_cost`` is not necessarily the implementation
        model's spend alone: a multi-phase review phase runs under
        ``cell.review_model`` (a separate ``ModelSpec``) and is folded into
        the same trace's total, and the open-ended track's LLM judge routes
        through the gateway under the cell's own trace id with no
        ``cost_mode`` of its own. So a cell is budget-exempt only if ALL of
        the following hold:

        - ``cell.model.cost_mode`` is ``CostMode.SUBSCRIPTION``,
        - ``cell.review_model`` is ``None``, or its ``cost_mode`` is also
          ``CostMode.SUBSCRIPTION``,
        - ``cell.task.track`` is not ``TaskTrack.OPEN_ENDED`` (the judge's
          calls are real, platform-billed API spend with no subscription
          coverage of its own).

        A cost cap that over-charges is safe; one that under-charges leaks
        real money. So this is deliberately all-or-nothing at the cell
        level: a cell that mixes subscription and platform spend (e.g. a
        subscription implementation model with a platform review model) is
        billed in full rather than split proportionally. True per-trace
        attribution — crediting only the subscription-covered portion —
        would need per-phase/per-component cost breakdown that is not
        available here; it is a deferred follow-up, not implemented by this
        fix.
        """
        review_ok = (
            cell.review_model is None or cell.review_model.cost_mode == CostMode.SUBSCRIPTION
        )
        return (
            cell.model.cost_mode == CostMode.SUBSCRIPTION
            and review_ok
            and cell.task.track != TaskTrack.OPEN_ENDED
        )

    def _billing_cell(self, cell: RunCell) -> RunCell:
        """Return the ``RunCell`` view to persist via ``save_result``.

        ``ResultsStore.save_result`` stamps the row's ``cost_mode`` from
        ``cell.model.cost_mode`` alone (it has no way to know about
        ``review_model`` or judge spend, and its signature is owned by
        another task). To keep the persisted ``cost_mode`` — and therefore
        ``get_billable_cost`` — in agreement with the budget arithmetic in
        ``_estimate_cell_cost``/``_reconcile_reservation`` (both driven by
        ``_is_budget_exempt``), this returns a shallow copy with
        ``model.cost_mode`` forced to ``CostMode.PLATFORM`` whenever the
        cell is not fully exempt but its own model claims
        ``CostMode.SUBSCRIPTION``. ``model.name`` — and therefore
        ``cell.cell_id`` — is untouched, so callers can use the returned
        cell purely for persistence without affecting identity.
        """
        if self._is_budget_exempt(cell) or cell.model.cost_mode != CostMode.SUBSCRIPTION:
            return cell
        return cell.model_copy(
            update={"model": cell.model.model_copy(update={"cost_mode": CostMode.PLATFORM})}
        )

    def _estimate_cell_cost(self, cell: RunCell) -> float:
        """Estimate the cost of a cell for budget reservation.

        Uses ``cell.budget`` if explicitly set. Otherwise derives a
        reasonable estimate from the run-level budget divided equally
        across all matrix cells (using the real cell count from
        ``build_matrix()``, not ``len(config.tasks)`` which is wrong
        when tasks is ``["*"]``).

        Budget-exempt cells (see ``_is_budget_exempt``) are zero-dollar:
        token usage is still tracked but does not count against the dollar
        budget.
        """
        if self._is_budget_exempt(cell):
            return 0.0
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
        self, cell: RunCell, actual_cost: float
    ) -> None:
        """Reconcile a cell's reservation against its actual cost.

        Adjusts the remaining *budget* by ``reserved - actual_cost``:
        refunds the difference back if actual cost was less than reserved,
        deducts the shortfall if it was more. Clears the reservation entry
        afterwards. The result is deliberately NOT floored at ``0.0``: a
        negative ``_remaining_budget`` records real overspend debt that
        must keep suppressing later cells. Under ``parallel_runs > 1``,
        several cells can hold outstanding reservations at once; flooring
        here would let one platform cell's overspend be silently erased by
        a *sibling's* unrelated refund, re-opening a cap that was already
        blown and admitting further real spend (this was tried and proven
        harmful — see the reservation check, which guards the zero-estimate
        case a different way instead).

        Budget-exempt cells (``_is_budget_exempt(cell)``) have their
        ``actual_cost`` zeroed here (F5), mirroring ``_estimate_cell_cost``,
        which already reserves $0 for them. Note this only affects the
        *budget* figure (``_remaining_budget``); it does not touch
        ``progress.total_cost``, which the caller re-derives from the store
        separately from the cell's true ``actual_cost`` and which stays the
        informational "what this would have cost" figure for every cell,
        exempt or not.
        """
        reserved = self._reservations.pop(cell.cell_id, 0.0)
        if self._is_budget_exempt(cell):
            actual_cost = 0.0
        if self._remaining_budget is not None:
            self._remaining_budget += reserved - actual_cost

    def _release_reservation(self, cell_id: str) -> None:
        """Release a cell's reservation back to the remaining budget.

        Used when a cell fails or is cancelled before recording a cost.
        """
        reserved = self._reservations.pop(cell_id, 0.0)
        if self._remaining_budget is not None and reserved > 0:
            self._remaining_budget += reserved

    async def _notify_progress(self) -> None:
        """Invoke the on_progress callback with a consistent snapshot.

        Must be called *outside* ``self._progress_lock``. The snapshot is
        built under the lock so the callback never sees a half-mutated
        state, then the callback is invoked without the lock held so a slow
        UI cannot block cell execution.
        """
        if self._on_progress is None:
            return
        async with self._progress_lock:
            snapshot = self.progress.snapshot()
        self._on_progress(snapshot)


class RetryableError(Exception):
    """Error that can be retried (rate limit, timeout, OOM)."""

    pass


async def _dry_run_cell(cell: RunCell) -> dict[str, Any]:
    """Placeholder cell runner for testing without real harnesses."""
    from harness_evaluator.gateway.models import TokenUsage

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
