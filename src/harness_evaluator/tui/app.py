"""Textual TUI app for harness-evaluator run command.

Layout:
  ┌─ Eval Log ──────────────────────────────┐
  │ (scrollable RichLog, auto-follows tail) │
  │                                         │
  ├─ Eval Progress ─────────────────────────┤
  │ (fixed ProgressFooter)                  │
  └─────────────────────────────────────────┘

Keyboard:
  q / Ctrl+C  — quit (cancels the run)
  d           — toggle DEBUG log level
  t           — toggle timestamps in log
  f           — toggle auto-follow (tail mode)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Header, RichLog

from harness_evaluator.tui.log_handler import TuiLogHandler
from harness_evaluator.tui.widgets import FooterState, ProgressFooter

if TYPE_CHECKING:
    from harness_evaluator.orchestrator.config import RunConfig
    from harness_evaluator.orchestrator.engine import OrchestratorProgress
    from harness_evaluator.orchestrator.results_store import ResultsStore


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
        ("q", "quit", "Quit"),
        ("d", "toggle_debug", "Debug"),
        ("t", "toggle_timestamps", "Timestamps"),
        ("f", "toggle_follow", "Follow"),
    ]

    def __init__(
        self,
        config: RunConfig,
        store: ResultsStore,
        run_cell_fn: Any,
        cells: list[Any],
    ) -> None:
        super().__init__()
        self._config = config
        self._store = store
        self._run_cell_fn = run_cell_fn
        self._cells = cells
        self._result: OrchestratorProgress | None = None
        self._log_handler: TuiLogHandler | None = None
        self._base_level: int = logging.INFO
        self._show_timestamps: bool = True
        self._auto_follow: bool = True

    @property
    def result(self) -> OrchestratorProgress | None:
        """The orchestrator result, available after the app exits."""
        return self._result

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="log-container"):
            yield RichLog(id="eval-log", markup=True, auto_scroll=True)
        yield ProgressFooter()

    def on_mount(self) -> None:
        """Set up logging and start the eval worker."""
        log_widget = self.query_one("#eval-log", RichLog)
        self._log_handler = TuiLogHandler(self, log_widget)
        self._log_handler.set_show_time(self._show_timestamps)
        self._log_handler.setLevel(self._base_level)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.handlers = [self._log_handler]

        # Start the orchestrator as a background worker.
        self._run_eval()

    def on_unmount(self) -> None:
        """Restore logging when the TUI exits."""
        root = logging.getLogger()
        root.handlers = []
        root.setLevel(logging.WARNING)

    @work(exclusive=True, exit_on_error=False)
    async def _run_eval(self) -> None:
        """Run the orchestrator inside a Textual worker."""
        from harness_evaluator.orchestrator.engine import Orchestrator

        footer = self.query_one(ProgressFooter)

        import time as _time

        footer_state = FooterState(
            total_cells=len(self._cells),
            budget=self._config.budget_usd,
            start_time=_time.monotonic(),
        )
        footer.state = footer_state

        def on_progress(snapshot: OrchestratorProgress) -> None:
            footer_state.completed = snapshot.completed
            footer_state.failed = snapshot.failed
            footer_state.skipped = snapshot.skipped
            footer_state.running = snapshot.running
            footer_state.total_cost = snapshot.total_cost
            footer_state.current_cell = snapshot.current_cell
            footer.state = footer_state

        orchestrator = Orchestrator(
            self._config, self._store, run_cell_fn=self._run_cell_fn, on_progress=on_progress
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
            self.exit()

    # --- Key bindings ---

    def action_toggle_debug(self) -> None:
        """Toggle between INFO and DEBUG log levels."""
        if self._base_level == logging.INFO:
            self._base_level = logging.DEBUG
        else:
            self._base_level = logging.INFO
        if self._log_handler:
            self._log_handler.setLevel(self._base_level)
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
