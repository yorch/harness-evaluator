"""Textual TUI app for harness-evaluator run command.

Layout:
  ┌─ Eval Log ──────────────────────────────┐
  │ (scrollable RichLog, auto-follows tail) │
  │                                         │
  ├─ Eval Progress ─────────────────────────┤
  │ (fixed ProgressFooter, docked with the  │
  │  key-bindings Footer below it — FooterBar) │
  │ q Quit (cancels the run)  d Toggle debug logging  ...  │
  └─────────────────────────────────────────┘

Keyboard (descriptions above are illustrative — see BINDINGS for the exact
text; also shown live in the bindings footer):
  q / Ctrl+C  — quit (cancels the run)
  d           — toggle DEBUG log level
  t           — toggle timestamps in log
  f           — toggle auto-follow (tail mode)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Header, RichLog

from harness_evaluator.tui.log_handler import TuiLogHandler
from harness_evaluator.tui.widgets import FooterBar, FooterState, ProgressFooter

if TYPE_CHECKING:
    from harness_evaluator.orchestrator.config import RunConfig
    from harness_evaluator.orchestrator.engine import OrchestratorProgress
    from harness_evaluator.orchestrator.results_store import ResultsStore

# Root of the harness-evaluator package logger tree, e.g. "harness_evaluator.orchestrator".
# Scoping the debug toggle to this logger (rather than the root logger) keeps
# third-party internals (aiohttp, docker, asyncio) out of the TUI at DEBUG level.
_PACKAGE_LOGGER_NAME = __name__.split(".")[0]


class EvalApp(App[object]):
    """Textual TUI for live eval monitoring.

    Runs the orchestrator as an async worker. Log records are bridged to a
    RichLog widget via TuiLogHandler. Progress snapshots update the footer.
    When the run completes (or is cancelled), the app exits and the caller
    prints the final summary to the regular console.
    """

    CSS = """
    #log-container {
        height: 1fr;
    }
    RichLog {
        border: solid $primary;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit (cancels the run)"),
        ("d", "toggle_debug", "Toggle debug logging"),
        ("t", "toggle_timestamps", "Toggle timestamps"),
        ("f", "toggle_follow", "Toggle auto-follow"),
    ]

    def __init__(
        self,
        config: RunConfig,
        store: ResultsStore,
        run_cell_fn: Any,
        cells: list[Any],
        verbose: int = 0,
    ) -> None:
        super().__init__()
        self._config = config
        self._store = store
        self._run_cell_fn = run_cell_fn
        self._cells = cells
        self._verbose = verbose
        self._result: OrchestratorProgress | None = None
        self._log_handler: TuiLogHandler | None = None
        # Initial TUI log level: -vv (verbose>=2) starts at DEBUG; INFO is the
        # floor otherwise. The 'd' key still toggles DEBUG on top of this.
        self._base_level: int = logging.DEBUG if verbose >= 2 else logging.INFO
        self._show_timestamps: bool = True
        self._auto_follow: bool = True
        self._footer_start_time: float | None = None
        self._previous_root_handlers: list[logging.Handler] = []
        self._previous_root_level: int = logging.WARNING
        self._previous_package_level: int = logging.NOTSET

    @property
    def result(self) -> OrchestratorProgress | None:
        """The orchestrator result, available after the app exits."""
        return self._result

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="log-container"):
            yield RichLog(id="eval-log", markup=True, auto_scroll=True)
        yield FooterBar()

    def on_mount(self) -> None:
        """Set up logging and start the eval worker."""
        log_widget = self.query_one("#eval-log", RichLog)
        self._log_handler = TuiLogHandler(self, log_widget)
        self._log_handler.set_show_time(self._show_timestamps)
        self._log_handler.setLevel(self._base_level)

        root = logging.getLogger()
        self._previous_root_handlers = list(root.handlers)
        self._previous_root_level = root.level
        root.handlers = [self._log_handler]

        # Scope the DEBUG toggle to the harness_evaluator logger tree so
        # pressing 'd' doesn't also surface aiohttp/docker/asyncio internals
        # (those inherit the root logger's level, which is left untouched).
        package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
        self._previous_package_level = package_logger.level
        package_logger.setLevel(self._base_level)

        # Start the orchestrator as a background worker.
        self._run_eval()

    def on_unmount(self) -> None:
        """Restore logging when the TUI exits."""
        root = logging.getLogger()
        if self._previous_root_handlers:
            # Copy, not alias: handing the live root logger the exact list
            # object we saved would let a later addHandler() call mutate our
            # own saved snapshot.
            root.handlers = list(self._previous_root_handlers)
        else:
            # No prior handlers to restore: on_mount never ran to capture
            # any (e.g. a Textual startup failure that panics before
            # reaching it), so this is a narrow edge case, not the common
            # case — cli.py's `run` command calls _configure_logging both
            # before AND after every TUI attempt, and the post-attempt call
            # replaces this handler immediately regardless. This is a
            # plainer StreamHandler with an explicit Formatter — not the
            # same handler as _configure_logging's RichHandler, just no
            # longer bare `%(message)s` with no level/logger/timestamp, for
            # the brief window (if any) before that call runs.
            fallback_handler = logging.StreamHandler()
            fallback_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
            )
            root.handlers = [fallback_handler]
        root.setLevel(self._previous_root_level)
        logging.getLogger(_PACKAGE_LOGGER_NAME).setLevel(self._previous_package_level)

    def _on_progress(self, snapshot: OrchestratorProgress) -> None:
        """Push a progress snapshot to the footer.

        Builds a brand-new ``FooterState`` (rather than mutating a shared
        instance and reassigning it) so Textual's reactive `!=` check on
        ``ProgressFooter.state`` fires for every snapshot — mutating a shared
        instance in place leaves the object identical to the reactive's
        cached value, so ``watch_state`` never runs again after the first
        assignment. This also drops the shared mutable state between this
        worker callback and the widget.
        """
        try:
            footer = self.query_one(ProgressFooter)
        except NoMatches:
            # The orchestrator's progress callback is fire-and-forget and can
            # land after the screen has torn down (e.g. a late callback
            # racing app exit); there is nothing to update at that point.
            return
        start_time = self._footer_start_time if self._footer_start_time is not None else (
            time.monotonic()
        )
        footer.state = FooterState(
            total_cells=snapshot.total_cells,
            completed=snapshot.completed,
            failed=snapshot.failed,
            skipped=snapshot.skipped,
            running=snapshot.running,
            total_cost=snapshot.total_cost,
            current_cell=snapshot.current_cell,
            running_cells=list(snapshot.running_cells),
            budget=self._config.budget_usd,
            start_time=start_time,
        )

    @work(exclusive=True, exit_on_error=False)
    async def _run_eval(self) -> None:
        """Run the orchestrator inside a Textual worker."""
        from harness_evaluator.orchestrator.engine import Orchestrator

        footer = self.query_one(ProgressFooter)

        self._footer_start_time = time.monotonic()
        footer.state = FooterState(
            total_cells=len(self._cells),
            budget=self._config.budget_usd,
            start_time=self._footer_start_time,
        )

        orchestrator = Orchestrator(
            self._config, self._store, run_cell_fn=self._run_cell_fn, on_progress=self._on_progress
        )
        try:
            self._result = await orchestrator.run()
        except asyncio.CancelledError:
            self._result = orchestrator.progress
            raise
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Eval worker failed")
            self._result = orchestrator.progress
        finally:
            # Auto-exit the app when the eval completes (or fails).
            # The caller (cli.py) reads app.result after app.run() returns.
            self.exit(return_code=self.return_code or 0)

    # --- Key bindings ---

    def action_toggle_debug(self) -> None:
        """Toggle between INFO and DEBUG log levels."""
        if self._base_level == logging.INFO:
            self._base_level = logging.DEBUG
        else:
            self._base_level = logging.INFO
        if self._log_handler:
            self._log_handler.setLevel(self._base_level)
        # Keep this scoped to the harness_evaluator logger tree (see on_mount)
        # so toggling doesn't also surface third-party DEBUG internals.
        logging.getLogger(_PACKAGE_LOGGER_NAME).setLevel(self._base_level)
        self._notify_level()

    def action_toggle_timestamps(self) -> None:
        """Toggle timestamp display in the log."""
        self._show_timestamps = not self._show_timestamps
        if self._log_handler:
            self._log_handler.set_show_time(self._show_timestamps)
        self._notify_toggle(
            "timestamps", self._show_timestamps
        )

    def action_toggle_follow(self) -> None:
        """Toggle auto-scroll (tail follow) on the log widget."""
        self._auto_follow = not self._auto_follow
        log_widget = self.query_one("#eval-log", RichLog)
        log_widget.auto_scroll = self._auto_follow
        self._notify_toggle("auto-follow", self._auto_follow)

    def _notify_level(self) -> None:
        level_name = logging.getLevelName(self._base_level)
        log_widget = self.query_one("#eval-log", RichLog)
        log_widget.write(
            f"[dim]--- log level: {level_name} ---[/dim]"
        )

    def _notify_toggle(self, feature: str, enabled: bool) -> None:
        state = "ON" if enabled else "OFF"
        log_widget = self.query_one("#eval-log", RichLog)
        log_widget.write(
            f"[dim]--- {feature}: {state} ---[/dim]"
        )
