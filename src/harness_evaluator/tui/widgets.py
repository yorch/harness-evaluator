"""Progress footer widget for the eval TUI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


@dataclass
class FooterState:
    """Snapshot of eval progress for the footer display."""

    total_cells: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    running: int = 0
    total_cost: float = 0.0
    current_cell: str | None = None
    budget: float | None = None
    start_time: float = field(default_factory=time.monotonic)

    @property
    def done(self) -> int:
        return self.completed + self.failed + self.skipped

    @property
    def progress_pct(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return self.done / self.total_cells * 100


class ProgressFooter(Widget):
    """Fixed-height footer showing eval progress (bar, counts, cost, cell)."""

    DEFAULT_CSS = """
    ProgressFooter {
        height: 6;
        dock: bottom;
        border-top: solid $primary;
        padding: 0 1;
        background: $surface;
    }
    """

    state: reactive[FooterState] = reactive(FooterState, layout=True)

    def __init__(self) -> None:
        super().__init__()
        self._static = Static()
        self._static.display = False

    def compose(self) -> ComposeResult:
        yield self._static

    def watch_state(self, state: FooterState) -> None:
        """Re-render the footer when state changes."""
        self._static.update(self._format_footer(state))
        self._static.display = True

    def _format_footer(self, state: FooterState) -> str:
        """Render the footer as a plain-text string."""
        done = state.done
        total = state.total_cells
        pct = state.progress_pct
        elapsed = time.monotonic() - state.start_time

        # Progress bar
        bar_width = 30
        filled = int(bar_width * done / total) if total else 0
        bar = "█" * filled + "░" * (bar_width - filled)

        # Counts
        counts = f"✓ {state.completed}  ✗ {state.failed}  ⊘ {state.skipped}  ► {state.running}"

        # Cost line
        if state.budget is not None:
            cost_line = f"Cost: ${state.total_cost:.4f} / ${state.budget:.2f}"
        else:
            cost_line = f"Cost: ${state.total_cost:.4f}"

        # Current cell line
        if state.running > 0 and state.current_cell:
            if state.running == 1:
                cell_line = f"Running: {state.current_cell}"
            else:
                cell_line = f"Running: {state.running} cells (last: {state.current_cell})"
        elif state.running > 0:
            cell_line = f"Running: {state.running} cells"
        else:
            cell_line = "Idle"

        return (
            f"{bar}  {done}/{total} ({pct:.1f}%)\n"
            f"{counts}\n"
            f"{cost_line}  |  Elapsed: {elapsed:.0f}s\n"
            f"{cell_line}"
        )
