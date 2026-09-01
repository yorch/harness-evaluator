"""Progress footer widget for the eval TUI."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Static

logger = logging.getLogger(__name__)

# Refresh interval (seconds) for elapsed-time ticking.
_FOOTER_REFRESH_INTERVAL = 1.0

# Maximum number of running cells to list individually.
_MAX_RUNNING_CELLS_SHOWN = 3

# _format_footer can emit up to 8 lines (3 header lines + "Running:" + up to
# _MAX_RUNNING_CELLS_SHOWN cell lines + "… and N more"). The widget's
# border-top consumes one row of its declared CSS height, so the height must
# be the max line count plus this border overhead for every line to render.
_FOOTER_BORDER_ROWS = 1
_FOOTER_HEIGHT = 3 + 1 + _MAX_RUNNING_CELLS_SHOWN + 1 + _FOOTER_BORDER_ROWS

# Fallback line width used when the widget hasn't been mounted/sized yet
# (e.g. unit tests calling _format_footer directly on a bare ProgressFooter()).
_DEFAULT_FOOTER_WIDTH = 80


def _ellipsize(text: str, width: int) -> str:
    """Truncate a single string to fit within width columns, appending an
    ellipsis if it was cut.

    Rich's default Static rendering wraps text that exceeds the render
    width instead of clipping it, which can push the "… and N more"
    overflow indicator (F7's must-always-be-visible line) out of the
    footer's fixed content rows on narrow terminals. This function only
    bounds the one string it is given — the actual "every line the footer
    emits fits in one row" guarantee comes from _format_footer and
    _format_running_cells calling this on every line they build, not from
    this function alone. (An earlier version of this docstring claimed that
    guarantee here; it was false, because only the running-cell lines were
    being ellipsized at the time — the header lines, including the fixed
    30-column progress bar, were not, and wrapped below ~46 columns.)
    """
    if width <= 0 or len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


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

    DEFAULT_CSS = f"""
    ProgressFooter {{
        height: {_FOOTER_HEIGHT};
        border-top: solid $primary;
        padding: 0 1;
        background: $surface;
    }}
    """

    # always_update=True: FooterState is an eq=True dataclass, so without this
    # a new-but-equal-valued snapshot would still be swallowed by the default
    # `current_value != value` gate (harmless in practice since every field
    # that differs between snapshots is copied and _tick redraws every
    # second regardless, but "fires for every snapshot" should be true, not
    # true-by-coincidence).
    state: reactive[FooterState] = reactive(FooterState, layout=True, always_update=True)

    def __init__(self) -> None:
        super().__init__()
        self._static = Static()
        self._static.display = False
        self._refresh_timer: Timer | None = None
        # Latches True after _tick logs a render fault, so a *persistent*
        # fault logs once instead of once per tick; cleared on any
        # successful render (in _tick or watch_state).
        self._render_fault_logged = False

    def compose(self) -> ComposeResult:
        yield self._static

    def on_mount(self) -> None:
        """Start the periodic refresh timer."""
        self._refresh_timer = self.set_interval(
            _FOOTER_REFRESH_INTERVAL, self._tick
        )

    def on_unmount(self) -> None:
        """Stop the periodic refresh timer."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def on_resize(self) -> None:
        """Re-render immediately on resize.

        _format_footer bakes the current width in at render time; without
        this, narrowing the terminal wraps text (and can hide the "… and N
        more" indicator) until the next periodic _tick, up to one
        _FOOTER_REFRESH_INTERVAL later.
        """
        self._tick()

    def _tick(self) -> None:
        """Re-render the footer for elapsed-time ticking.

        A formatting error must never stop the timer: ``set_interval``
        re-arms itself automatically, but if this callback raised, Textual
        would treat the interval as failed and stop calling it.
        """
        if not self._static.display:
            return
        try:
            rendered = self._format_footer(self.state)
        except Exception:
            # Never let a formatting fault silently freeze the footer with
            # no breadcrumb — but log it once (until a render succeeds
            # again), not on every tick: at the production 1s interval a
            # persistent fault would otherwise write one record per second
            # into the log pane for the rest of the run once DEBUG is on.
            if not self._render_fault_logged:
                logger.debug("footer render failed", exc_info=True)
                self._render_fault_logged = True
            return
        self._render_fault_logged = False
        self._static.update(rendered)

    def watch_state(self, state: FooterState) -> None:
        """Re-render the footer when state changes."""
        self._static.update(self._format_footer(state))
        self._static.display = True
        self._render_fault_logged = False

    def _content_width(self) -> int:
        """The widget's current usable text width, falling back to a sane
        default when unmounted/unsized (e.g. bare unit-test construction)."""
        width = self.content_size.width
        return width if width > 0 else _DEFAULT_FOOTER_WIDTH

    def _format_footer(self, state: FooterState, width: int | None = None) -> str:
        """Render the footer as a plain-text string.

        ``width`` (defaulting to the widget's current content width) bounds
        *every* emitted line — bar/counts/cost header lines here, and the
        running-cell lines via _format_running_cells — so none of them can
        ever wrap. An earlier version only bounded the running-cell lines;
        the header lines (the progress bar alone is a fixed 30 columns) then
        wrapped below ~46 columns and evicted the "… and N more" indicator
        just the same, only via a different line. See _ellipsize.
        """
        line_width = width if width is not None else self._content_width()
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

        # Cost line. `state.total_cost` is the informational "true cost of
        # every cell" figure — it is NOT the figure charged against
        # `state.budget` (budget-exempt cells, e.g. subscription-mode, are
        # excluded from the budget arithmetic but still counted here), so
        # the two are shown as separate labelled figures rather than a
        # "spent / cap" pair. Mirrors cli.py's `_render_progress_panel`,
        # abbreviated to fit the footer's width budget.
        if state.budget is not None:
            cost_line = f"Cost: ${state.total_cost:.4f} (info) | Cap: ${state.budget:.2f}"
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

        # Running cells lines (already ellipsized per sub-line)
        running_lines = self._format_running_cells(state, line_width)

        header_lines = [
            f"{bar}  {done}/{total} ({pct:.1f}%)",
            counts,
            f"{cost_line}  |  Elapsed: {elapsed_str}",
        ]
        lines = [_ellipsize(line, line_width) for line in header_lines]
        lines.append(running_lines)
        return "\n".join(lines)

    def _format_running_cells(self, state: FooterState, width: int) -> str:
        """Format the running-cells section of the footer.

        Every line is ellipsized to ``width`` so Rich can never wrap it —
        an unbounded line pushes the "… and N more" indicator (which must
        always be visible, per F7) out of the footer's fixed content rows.
        """
        if state.running == 0:
            return _ellipsize("Idle", width)

        cells = state.running_cells if state.running_cells else (
            [state.current_cell] if state.current_cell else []
        )

        if not cells:
            return _ellipsize(f"Running: {state.running} cells", width)

        shown = cells[:_MAX_RUNNING_CELLS_SHOWN]
        lines = [_ellipsize("Running:", width)]
        for cell_id in shown:
            lines.append(_ellipsize(f"  ► {_format_cell_id(cell_id)}", width))

        remaining = len(cells) - len(shown)
        if remaining > 0:
            lines.append(_ellipsize(f"  … and {remaining} more", width))

        return "\n".join(lines)


class FooterBar(Vertical):
    """Bottom chrome bar holding both the progress footer and the
    key-bindings footer, docked together as a single unit.

    Textual's ``Footer`` widget self-docks ``bottom`` by default; docking
    ``ProgressFooter`` too (its own previous behaviour) made two independent
    dock:bottom siblings, and Textual's dock arrangement computes each
    docked widget's position independently from the container's *full*
    height rather than stacking them — the two overlapped by one row. This
    container is docked bottom exactly once, and disables the child
    ``Footer``'s own dock so both widgets simply stack in normal flow
    *inside* this single docked region: ``ProgressFooter`` above, the
    key-bindings ``Footer`` below it.
    """

    DEFAULT_CSS = """
    FooterBar {
        dock: bottom;
        height: auto;
    }
    FooterBar Footer {
        dock: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield ProgressFooter()
        yield Footer()
