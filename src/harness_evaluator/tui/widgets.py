"""Progress footer widget for the eval TUI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

# Refresh interval (seconds) for elapsed-time ticking.
_FOOTER_REFRESH_INTERVAL = 1.0

# Maximum number of running cells to list individually.
_MAX_RUNNING_CELLS_SHOWN = 3


def _format_cell_id(cell_id: str) -> str:
    """Make a cell_id more readable for the footer.

    Cell IDs look like ``claude-code__sonnet__swe-001__r0``.
    Replace ``__`` with `` | `` and strip the repeat prefix ``r``
    to just the number for compactness.
    """
    parts = cell_id.split("__")
    # Shorten repeat: r0 -> rep 0
    if len(parts) >= 1 and parts[-1].startswith("r") and parts[-1][1:].isdigit():
        parts[-1] = f"rep {parts[-1][1:]}"
    return " | ".join(parts)


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
    running_cells: list[str] = field(default_factory=list)
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
    """Fixed-height footer showing eval progress (bar, counts, cost, cells).

    A periodic timer re-renders the footer every second so the elapsed
    time ticks even when no progress events arrive (e.g. during a
    long-running cell).
    """

    DEFAULT_CSS = """
    ProgressFooter {
        height: 7;
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
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield self._static

    def on_mount(self) -> None:
        """Start the periodic refresh timer."""
        self._refresh_timer = self.set_timer(
            _FOOTER_REFRESH_INTERVAL, self._tick
        )

    def on_unmount(self) -> None:
        """Stop the periodic refresh timer."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _tick(self) -> None:
        """Re-render the footer for elapsed-time ticking, then reschedule."""
        if self._static.display:
            self._static.update(self._format_footer(self.state))
        self._refresh_timer = self.set_timer(
            _FOOTER_REFRESH_INTERVAL, self._tick
        )

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

        # Elapsed time formatted as M:SS or H:MM:SS
        if elapsed >= 3600:
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            s = int(elapsed % 60)
            elapsed_str = f"{h}:{m:02d}:{s:02d}"
        else:
            elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"

        # Running cells lines
        running_lines = self._format_running_cells(state)

        return (
            f"{bar}  {done}/{total} ({pct:.1f}%)\n"
            f"{counts}\n"
            f"{cost_line}  |  Elapsed: {elapsed_str}\n"
            f"{running_lines}"
        )

    def _format_running_cells(self, state: FooterState) -> str:
        """Format the running-cells section of the footer."""
        if state.running == 0:
            return "Idle"

        cells = state.running_cells if state.running_cells else (
            [state.current_cell] if state.current_cell else []
        )

        if not cells:
            return f"Running: {state.running} cells"

        shown = cells[:_MAX_RUNNING_CELLS_SHOWN]
        lines = []
        for cell_id in shown:
            lines.append(f"  ► {_format_cell_id(cell_id)}")

        remaining = len(cells) - len(shown)
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

        return "Running:\n" + "\n".join(lines)
