"""Progress footer widget for the eval TUI."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Static

from harness_evaluator.runner.redaction import StreamingRedactor

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
    cell_phases: dict[str, str] = field(default_factory=dict)
    cell_api_stats: dict[str, tuple[int, float]] = field(default_factory=dict)

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
    long-running cell). The tick also polls the results store and gateway
    store for per-cell phase and API call stats, updating ``FooterState``
    in place so the footer reflects sub-cell activity without callbacks.
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
        # Optional callback invoked on each tick to poll live per-cell
        # data (phases, API stats) and update the footer state in place.
        # Set by EvalApp.on_mount; None in unit tests.
        self._tick_callback: Callable[[FooterState], None] | None = None

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

        The tick also invokes the optional ``_tick_callback`` to poll
        live per-cell data (phases, API stats) from the results store
        and gateway store, updating the footer state in place before
        rendering. The callback is wrapped in try/except so a polling
        failure (e.g. SQLite ``database is locked``) never freezes the
        footer.
        """
        if not self._static.display:
            return
        if self._tick_callback is not None:
            try:
                self._tick_callback(self.state)
            except Exception:
                logger.debug("footer tick callback failed", exc_info=True)
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
        """Re-render the footer when state changes.

        Mirrors ``_tick``'s defensive rendering: a formatting error
        must never crash the footer or stop the reactive watcher.
        """
        self._static.display = True
        try:
            rendered = self._format_footer(state)
        except Exception:
            if not self._render_fault_logged:
                logger.debug("footer render failed in watch_state", exc_info=True)
                self._render_fault_logged = True
            return
        self._render_fault_logged = False
        self._static.update(rendered)

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

        When phase info is available (polled from the results store on the
        tick timer), each running cell line shows its current phase, e.g.
        ``► claude | sonnet | swe-001 | rep 0 [harness_running]``.
        When API call stats are available, an additional ``(N calls, $X)``
        suffix is appended.
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
            label = f"  ► {_format_cell_id(cell_id)}"
            phase = state.cell_phases.get(cell_id)
            if phase:
                label += f" [{phase}]"
            api_stats = state.cell_api_stats.get(cell_id)
            if api_stats:
                calls, cost = api_stats
                label += f" ({calls} calls, ${cost:.4f})"
            lines.append(_ellipsize(label, width))

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


# Maximum number of lines retained per cell in the output panel. Older
# lines are dropped (ring buffer) to bound memory usage during long runs.
MAX_CELL_OUTPUT_LINES = 200


class CellOutputPanel(Widget):
    """Bounded per-cell harness output panel.

    Displays redacted, line-buffered harness stdout/stderr for a
    selected cell. Output is fed via ``feed()`` from the runner's
    streaming callback, redacted line-by-line via
    ``StreamingRedactor``. Only one cell is shown at a time; the user
    cycles through running cells with the ``o`` key (wired in
    ``EvalApp``).

    The panel is opt-in: it is hidden by default and only shown when
    the user presses ``o``. It uses a separate ``RichLog`` (not the
    shared ``#eval-log``) with ``markup=False`` so raw harness output
    is never interpreted as Rich markup, preventing ANSI/markup
    injection from the harness.

    Retention is bounded to ``MAX_CELL_OUTPUT_LINES`` per cell; older
    lines are dropped (ring buffer) to bound memory during long runs.
    """

    DEFAULT_CSS = """
    CellOutputPanel {
        height: 1fr;
        min-height: 3;
        display: none;
        border: solid $accent;
    }
    CellOutputPanel.-visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        # markup=False so raw harness output is never interpreted as Rich
        # markup — a harness could output "[red]sk-ant-...[/red]" and the
        # redaction regex would not catch the split token, but Rich would
        # strip the tags and render the secret. With markup=False the
        # tags are displayed literally and the secret stays fragmented.
        self._static = Static(markup=False)
        # Per-cell ring buffer of redacted lines.
        self._cell_lines: dict[str, list[str]] = {}
        # Currently selected cell ID for display.
        self._selected_cell: str | None = None
        # Per-cell streaming redactors (for incomplete line buffering).
        self._redactors: dict[str, StreamingRedactor] = {}

    def compose(self) -> ComposeResult:
        yield self._static

    def select_cell(self, cell_id: str | None) -> None:
        """Select which cell's output to display, or None to hide."""
        self._selected_cell = cell_id
        if cell_id is None:
            self.remove_class("-visible")
        else:
            self.add_class("-visible")
        self._refresh()

    def cycle_cell(self, running_cells: list[str]) -> None:
        """Cycle to the next running cell, or hide if none."""
        if not running_cells:
            self.select_cell(None)
            return
        if self._selected_cell is None or self._selected_cell not in running_cells:
            self.select_cell(running_cells[0])
        else:
            idx = running_cells.index(self._selected_cell)
            next_idx = (idx + 1) % len(running_cells)
            self.select_cell(running_cells[next_idx])

    def feed(self, cell_id: str, stream_name: str, data: bytes) -> None:
        """Feed raw bytes from the runner's streaming callback.

        Redacts line-by-line and appends to the cell's ring buffer.
        If the fed cell is currently selected, refreshes the display.
        """
        if cell_id not in self._redactors:
            self._redactors[cell_id] = StreamingRedactor()
        redactor = self._redactors[cell_id]
        lines = redactor.feed(data)
        if cell_id not in self._cell_lines:
            self._cell_lines[cell_id] = []
        buf = self._cell_lines[cell_id]
        for line in lines:
            prefix = "" if stream_name == "stdout" else "[stderr] "
            buf.append(f"{prefix}{line}")
        # Trim to retention limit (ring buffer).
        if len(buf) > MAX_CELL_OUTPUT_LINES:
            self._cell_lines[cell_id] = buf[-MAX_CELL_OUTPUT_LINES:]
        if cell_id == self._selected_cell:
            self._refresh()

    def flush_cell(self, cell_id: str) -> None:
        """Flush any remaining buffered output for a cell."""
        redactor = self._redactors.get(cell_id)
        if redactor is None:
            return
        remaining = redactor.flush()
        if remaining and cell_id in self._cell_lines:
            self._cell_lines[cell_id].append(remaining)
        if cell_id == self._selected_cell:
            self._refresh()

    def clear_cell(self, cell_id: str) -> None:
        """Remove all buffered output for a cell (e.g. on completion)."""
        self._cell_lines.pop(cell_id, None)
        self._redactors.pop(cell_id, None)
        if cell_id == self._selected_cell:
            self._refresh()

    def _refresh(self) -> None:
        """Re-render the panel for the currently selected cell.

        Uses ``rich.text.Text`` objects for the header (so styling works
        even though ``markup=False``), and plain strings for the output
        lines (so they are displayed literally, never interpreted as
        Rich markup).

        Safe to call when the widget is not mounted (e.g. in unit tests):
        ``Static.update`` requires an active app context, so we skip the
        visual update when not mounted and rely on the next mount/refresh
        to render the current state.
        """
        from rich.text import Text

        if self._selected_cell is None:
            if self._static.is_mounted:
                self._static.update("")
            return
        lines = self._cell_lines.get(self._selected_cell, [])
        if not lines:
            content: Text | str = Text.assemble(
                (f"Cell {self._selected_cell}: ", "dim"),
                ("(no output yet)", "dim"),
            )
        else:
            header = Text.assemble(
                ("Output: ", "bold"),
                (self._selected_cell, "bold"),
                "\n",
            )
            body = "\n".join(lines)
            content = Text.assemble(header, body)
        if self._static.is_mounted:
            self._static.update(content)
