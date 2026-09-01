"""Tests for the TUI progress footer widget."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time

import pytest
from textual.app import App, ComposeResult

from harness_evaluator.tui.widgets import (
    _FOOTER_BORDER_ROWS,
    _FOOTER_HEIGHT,
    _MAX_RUNNING_CELLS_SHOWN,
    FooterState,
    ProgressFooter,
    _ellipsize,
    _format_cell_id,
)


class _FooterHostApp(App[None]):
    """Minimal host app so ProgressFooter can be tested through the real
    Textual widget lifecycle (mount/reactive/timer), not just as a bare
    Python object."""

    def compose(self) -> ComposeResult:
        yield ProgressFooter()


def _rendered_texts(app: App[None]) -> list[str]:
    """Extract the plain text of every rendered glyph run from a real
    App.run_test() screenshot — for assertions against what the compositor
    actually drew, not just what a string-building helper produced."""
    svg = app.export_screenshot()
    raw = re.findall(r"<text[^>]*>(.*?)</text>", svg)
    return [html.unescape(t).replace("\xa0", " ") for t in raw]


# Realistic long cell IDs (harness/model/task-name lengths seen in this
# repo's own task library), matching the adversarial review's own repro.
_LONG_RUNNING_CELLS = [
    "claude-code__claude-sonnet-4-5-20250929__swe-django-12345__r0",
    "opencode__gpt-4o-2024-11-20__open-ended-refactor-legacy__r1",
    "codex__o4-mini-2025-04-16__swe-astropy-14995__r2",
    "aider__claude-opus-4-20250514__swe-flask-9981__r3",
]


class TestFooterState:
    """Tests for the FooterState dataclass."""

    def test_defaults(self):
        """FooterState starts with zeroed values."""
        state = FooterState()
        assert state.total_cells == 0
        assert state.completed == 0
        assert state.failed == 0
        assert state.skipped == 0
        assert state.running == 0
        assert state.total_cost == 0.0
        assert state.current_cell is None
        assert state.running_cells == []
        assert state.budget is None

    def test_done_property(self):
        """done = completed + failed + skipped."""
        state = FooterState(completed=10, failed=5, skipped=3)
        assert state.done == 18

    def test_done_zero(self):
        """done is 0 when all counts are 0."""
        state = FooterState()
        assert state.done == 0

    def test_progress_pct(self):
        """progress_pct = done / total * 100."""
        state = FooterState(total_cells=100, completed=25, failed=5)
        assert state.progress_pct == 30.0

    def test_progress_pct_zero_total(self):
        """progress_pct is 0 when total_cells is 0."""
        state = FooterState(total_cells=0)
        assert state.progress_pct == 0.0

    def test_progress_pct_full(self):
        """progress_pct is 100 when all cells are done."""
        state = FooterState(total_cells=10, completed=10)
        assert state.progress_pct == 100.0


class TestFormatCellId:
    """Tests for _format_cell_id helper."""

    def test_basic_cell_id(self):
        """Cell ID with harness, model, task, repeat is split nicely."""
        result = _format_cell_id("claude-code__sonnet__swe-001__r0")
        assert result == "claude-code | sonnet | swe-001 | rep 0"

    def test_cell_id_with_review_model(self):
        """Cell ID with review model suffix is handled."""
        result = _format_cell_id("claude-code__sonnet__swe-001__r0__rev-opus")
        # Last part doesn't start with 'r' + digits, so it stays
        assert "claude-code" in result
        assert "sonnet" in result
        assert "rev-opus" in result

    def test_no_repeat_suffix(self):
        """Cell ID without repeat suffix is still formatted."""
        result = _format_cell_id("opencode__gpt-4o__task-1")
        assert result == "opencode | gpt-4o | task-1"

    def test_double_digit_repeat(self):
        """Double-digit repeat numbers work."""
        result = _format_cell_id("h__m__t__r12")
        assert result == "h | m | t | rep 12"


class TestProgressFooter:
    """Tests for the ProgressFooter widget."""

    def test_format_footer_basic(self):
        """_format_footer produces a string with bar, counts, cost, cell."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=100,
            completed=50,
            failed=10,
            skipped=5,
            running=2,
            total_cost=1.50,
            current_cell="claude-code__claude-sonnet-5__swe-001__r0",
            running_cells=["claude-code__claude-sonnet-5__swe-001__r0"],
            budget=100.0,
            start_time=time.monotonic(),
        )
        result = footer._format_footer(state)
        assert "65/100" in result
        assert "65.0%" in result
        assert "✓ 50" in result
        assert "✗ 10" in result
        assert "⊘ 5" in result
        assert "► 2" in result
        assert "$1.5000" in result
        assert "$100.00" in result
        assert "claude-code" in result
        assert "swe-001" in result

    def test_format_footer_no_budget(self):
        """_format_footer omits budget when None."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=10,
            completed=5,
            total_cost=0.25,
            budget=None,
        )
        result = footer._format_footer(state)
        assert "$0.2500" in result
        assert "/" not in result.split("Cost:")[1].split("|")[0]

    def test_format_footer_does_not_conflate_informational_total_with_cap(self):
        """The cost line must not read as "spent / cap": `total_cost` is
        the informational figure (includes budget-exempt cells), not what
        is charged against `budget`. Mirrors cli.py's summary/progress
        panel wording so the TUI footer and the CLI output agree."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=10,
            completed=5,
            total_cost=12.3456,
            budget=5.00,
        )
        result = footer._format_footer(state)
        assert "$12.3456" in result
        assert "$5.00" in result
        # Not a "spent / cap" fraction -- no "/" between the two dollar
        # figures on the cost line.
        cost_line = next(line for line in result.split("\n") if "Cost:" in line)
        assert "/" not in cost_line
        assert "$12.3456 / $5.00" not in result

    def test_format_footer_idle(self):
        """_format_footer shows 'Idle' when no cells running."""
        footer = ProgressFooter()
        state = FooterState(total_cells=10, completed=10)
        result = footer._format_footer(state)
        assert "Idle" in result

    def test_format_footer_running_single(self):
        """_format_footer shows single running cell with nice formatting."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=10,
            running=1,
            current_cell="claude-code__sonnet__swe-001__r0",
            running_cells=["claude-code__sonnet__swe-001__r0"],
        )
        result = footer._format_footer(state)
        assert "Running:" in result
        assert "claude-code" in result
        assert "sonnet" in result
        assert "swe-001" in result
        assert "rep 0" in result

    def test_format_footer_running_multiple(self):
        """_format_footer shows all running cells (up to max)."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=10,
            running=3,
            current_cell="cell-3",
            running_cells=["cell-1", "cell-2", "cell-3"],
        )
        result = footer._format_footer(state)
        assert "Running:" in result
        assert "cell-1" in result
        assert "cell-2" in result
        assert "cell-3" in result

    def test_format_footer_running_more_than_max(self):
        """_format_footer truncates running cells and shows 'and N more'."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=100,
            running=5,
            current_cell="cell-5",
            running_cells=["cell-1", "cell-2", "cell-3", "cell-4", "cell-5"],
        )
        result = footer._format_footer(state)
        assert "and 2 more" in result

    def test_format_footer_running_no_cell_ids(self):
        """_format_footer shows running count without cell IDs."""
        footer = ProgressFooter()
        state = FooterState(total_cells=10, running=2, current_cell=None)
        result = footer._format_footer(state)
        assert "Running: 2 cells" in result

    def test_format_footer_progress_bar(self):
        """_format_footer includes a progress bar with block chars."""
        footer = ProgressFooter()
        state = FooterState(total_cells=100, completed=50)
        result = footer._format_footer(state)
        assert "█" in result
        assert "░" in result

    def test_format_footer_zero_total(self):
        """_format_footer handles zero total cells gracefully."""
        footer = ProgressFooter()
        state = FooterState(total_cells=0)
        result = footer._format_footer(state)
        assert "0/0" in result
        assert "0.0%" in result

    def test_format_footer_elapsed_time(self):
        """_format_footer includes elapsed time in M:SS format."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=10,
            start_time=time.monotonic() - 65,  # 65 seconds ago
        )
        result = footer._format_footer(state)
        assert "Elapsed:" in result
        # 65 seconds = 1:05
        assert "1:05" in result

    def test_format_footer_elapsed_time_hours(self):
        """_format_footer shows H:MM:SS format for >= 1 hour."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=100,
            start_time=time.monotonic() - 3725,  # 1h 2m 5s
        )
        result = footer._format_footer(state)
        assert "Elapsed:" in result
        assert "1:02:05" in result

    def test_format_footer_falls_back_to_current_cell(self):
        """_format_footer uses current_cell when running_cells is empty."""
        footer = ProgressFooter()
        state = FooterState(
            total_cells=10,
            running=1,
            current_cell="opencode__gpt-4o__task-1__r0",
            running_cells=[],
        )
        result = footer._format_footer(state)
        assert "opencode" in result
        assert "gpt-4o" in result


class TestProgressFooterHeight:
    """F7: the footer's rendered content must always fit its configured height."""

    @pytest.mark.parametrize("n_running", [0, 1, 2, 3, 4, 5, 10])
    def test_line_count_never_exceeds_content_height(self, n_running):
        """_format_footer's line count must fit the widget's usable content
        rows (declared height minus the border-top row), for any number of
        running cells — including the >3-running-cells overflow case."""
        footer = ProgressFooter()
        running_cells = [f"cell-{i}" for i in range(n_running)]
        state = FooterState(
            total_cells=100,
            running=n_running,
            current_cell=running_cells[-1] if running_cells else None,
            running_cells=running_cells,
        )
        rendered = footer._format_footer(state)
        line_count = len(rendered.split("\n"))
        assert line_count <= _FOOTER_HEIGHT - _FOOTER_BORDER_ROWS

    def test_and_more_line_survives_the_height_budget(self):
        """The '… and N more' line is the only signal that cells are
        hidden — it must always be within the widget's visible content
        rows, not just present in the formatted string."""
        running_cells = [f"cell-{i}" for i in range(_MAX_RUNNING_CELLS_SHOWN + 2)]
        footer = ProgressFooter()
        state = FooterState(
            total_cells=100,
            running=len(running_cells),
            current_cell=running_cells[-1],
            running_cells=running_cells,
        )
        rendered = footer._format_footer(state)
        lines = rendered.split("\n")
        assert any("more" in line for line in lines)
        assert len(lines) <= _FOOTER_HEIGHT - _FOOTER_BORDER_ROWS


class TestEllipsize:
    """Unit tests for the _ellipsize truncation helper."""

    def test_short_text_is_unchanged(self):
        assert _ellipsize("short", 20) == "short"

    def test_exact_width_is_unchanged(self):
        assert _ellipsize("exact", 5) == "exact"

    def test_long_text_is_truncated_with_ellipsis(self):
        result = _ellipsize("this is a long line that needs cutting", 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_width_one_is_just_the_ellipsis(self):
        assert _ellipsize("anything", 1) == "…"

    def test_non_positive_width_returns_text_unchanged(self):
        assert _ellipsize("anything", 0) == "anything"


# A FooterState that stresses the header lines too (budget/cost/counts), not
# just the running-cell lines — the round-2 regression (header lines were
# never ellipsized) only shows up once the cost line and counts are
# non-trivial, matching the adversarial review's own repro.
def _stressed_state() -> FooterState:
    return FooterState(
        total_cells=100,
        completed=7,
        failed=1,
        running=len(_LONG_RUNNING_CELLS),
        total_cost=1.2345,
        budget=100.0,
        current_cell=_LONG_RUNNING_CELLS[-1],
        running_cells=list(_LONG_RUNNING_CELLS),
    )


class TestProgressFooterNarrowWidth:
    """F7 (Important 3): the '… and N more' overflow indicator must remain
    visible even when long running-cell lines *or* the header lines (bar /
    counts / cost) would otherwise wrap on a narrow terminal. These render
    through the real compositor (App.run_test(size=...)), not just
    string/line-count assertions — Rich wraps unbounded text regardless of
    what _format_footer's `\n` count says, so only a real render can catch
    that."""

    @pytest.mark.parametrize("width", [30, 40, 46, 60, 70, 80, 100])
    async def test_and_more_line_is_rendered_at_narrow_widths(self, width):
        """Covers the round-2 regression directly: at these widths the
        running-cell lines alone were already ellipsized and fit, but the
        unellipsized bar/counts/cost header lines wrapped instead, evicting
        the same overflow indicator via a different line. 30 and 40 are the
        widths the re-review's own repro used; 46 and up were already
        passing before this fix."""
        app = _FooterHostApp()
        async with app.run_test(size=(width, 24)) as pilot:
            footer = app.query_one(ProgressFooter)
            footer.state = _stressed_state()
            await pilot.pause()

            rendered = " ".join(_rendered_texts(app))
            assert "more" in rendered, (
                f"'… and N more' was not visible at width={width}: {rendered!r}"
            )

    async def test_and_more_line_is_rendered_at_the_documented_floor_width(self):
        """There IS a genuine floor below which the overflow indicator's own
        text can't survive: ellipsizing truncates from the right, and "  …
        and 1 more" is itself 14 columns — narrower than that and the
        ellipsis eats into the word "more". 16 terminal columns (14 content
        columns, after the 2-column horizontal padding) is the floor for a
        single-digit remaining count; test at exactly that floor rather than
        claiming (falsely) that any width works."""
        app = _FooterHostApp()
        async with app.run_test(size=(16, 24)) as pilot:
            footer = app.query_one(ProgressFooter)
            footer.state = _stressed_state()
            await pilot.pause()

            assert footer.content_size.width == 14
            rendered = " ".join(_rendered_texts(app))
            assert "more" in rendered, (
                f"'… and N more' was not visible at the documented floor: {rendered!r}"
            )

    @pytest.mark.parametrize("width", [30, 40, 60])
    async def test_format_footer_lines_never_exceed_the_render_width(self, width):
        """Every emitted line — header lines included — must fit within the
        widget's actual content width, not just be one of a bounded number
        of `\n`-separated strings."""
        app = _FooterHostApp()
        async with app.run_test(size=(width, 24)) as pilot:
            footer = app.query_one(ProgressFooter)
            state = _stressed_state()
            footer.state = state
            await pilot.pause()

            content_width = footer.content_size.width
            assert content_width > 0
            rendered = footer._format_footer(state)
            for line in rendered.split("\n"):
                assert len(line) <= content_width, (
                    f"line {line!r} exceeds content width {content_width}"
                )


class TestProgressFooterReactiveLifecycle:
    """F6: the state reactive must fire watch_state for every distinct
    progress snapshot, using the real Textual widget lifecycle."""

    async def test_watch_state_fires_for_successive_new_state_objects(self):
        app = _FooterHostApp()
        async with app.run_test() as pilot:
            footer = app.query_one(ProgressFooter)
            seen: list[int] = []
            original_watch_state = footer.watch_state

            def spy_watch_state(state: FooterState) -> None:
                seen.append(state.completed)
                original_watch_state(state)

            footer.watch_state = spy_watch_state  # type: ignore[method-assign]

            for completed in (1, 2, 3):
                footer.state = FooterState(total_cells=10, completed=completed)
                await pilot.pause()

            assert seen == [1, 2, 3]

    async def test_watch_state_fires_even_for_an_equal_valued_new_object(self):
        """FooterState is an eq=True dataclass, so a new-but-equal-valued
        object would still be swallowed by the reactive's default `!=`
        gate. always_update=True on the `state` reactive makes "fires for
        every snapshot" true unconditionally, not just when the fields
        happen to differ."""
        app = _FooterHostApp()
        async with app.run_test() as pilot:
            footer = app.query_one(ProgressFooter)

            # Pin start_time explicitly: FooterState's default_factory=time.monotonic
            # would otherwise make every construction unequal, defeating the point.
            first = FooterState(total_cells=10, completed=1, start_time=0.0)
            footer.state = first
            await pilot.pause()

            seen: list[FooterState] = []
            original_watch_state = footer.watch_state

            def spy_watch_state(state: FooterState) -> None:
                seen.append(state)
                original_watch_state(state)

            footer.watch_state = spy_watch_state  # type: ignore[method-assign]

            second = FooterState(total_cells=10, completed=1, start_time=0.0)
            assert second == first
            assert second is not first
            footer.state = second
            await pilot.pause()

            assert len(seen) == 1


class TestProgressFooterTimer:
    """F11: the refresh timer must survive a formatting error and must not
    be re-created (chained set_timer) on every tick."""

    async def test_tick_reuses_the_same_interval_timer(self):
        """A healthy tick must not replace the interval timer object — that
        would be the old chained set_timer/set_timer re-arm pattern."""
        app = _FooterHostApp()
        async with app.run_test():
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(total_cells=10, completed=1)
            timer_before = footer._refresh_timer
            assert timer_before is not None

            footer._tick()
            footer._tick()

            assert footer._refresh_timer is timer_before

    async def test_tick_survives_a_formatting_error(self):
        """An exception raised while formatting must not propagate out of
        _tick, and a later, healthy tick must still update the footer."""
        app = _FooterHostApp()
        async with app.run_test():
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(total_cells=10, completed=1)

            def boom(_state: FooterState) -> str:
                raise ValueError("formatting exploded")

            footer._format_footer = boom  # type: ignore[method-assign]
            footer._tick()  # must not raise

            def working(_state: FooterState) -> str:
                return "still ticking"

            footer._format_footer = working  # type: ignore[method-assign]
            footer._tick()

            assert str(footer._static.content) == "still ticking"

    async def test_tick_fires_repeatedly_over_time(self, monkeypatch):
        """The refresh must actually repeat (set_interval), not fire once
        and stop (a one-shot set_timer). Reverting on_mount's set_interval
        to set_timer leaves every other tui test green — this is the only
        test that measures repetition over real elapsed time.

        Counts calls to _format_footer rather than wrapping _tick itself:
        Timer.set_interval/set_timer captures the *bound method object*
        passed to it at call time, so reassigning footer._tick afterward
        would not intercept the timer's already-registered callback.
        _format_footer, by contrast, is looked up fresh via `self.` on
        every _tick invocation, so patching it here does intercept.
        """
        import harness_evaluator.tui.widgets as widgets_module

        monkeypatch.setattr(widgets_module, "_FOOTER_REFRESH_INTERVAL", 0.03)

        app = _FooterHostApp()
        async with app.run_test():
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(total_cells=10, completed=1)

            call_count = 0
            original_format_footer = footer._format_footer

            def counting_format_footer(state: FooterState) -> str:
                nonlocal call_count
                call_count += 1
                return original_format_footer(state)

            footer._format_footer = counting_format_footer  # type: ignore[method-assign]

            await asyncio.sleep(0.3)

            assert call_count >= 3, (
                f"expected the interval to fire repeatedly, only got {call_count} ticks"
            )

    async def test_tick_logs_at_debug_on_formatting_failure(self, caplog):
        """A formatting fault must leave a breadcrumb, not fail silently —
        the try/except is deliberately narrow and necessary (a raising
        interval callback would otherwise crash the app), but swallowing
        with zero diagnostics hides a permanently frozen footer."""
        app = _FooterHostApp()
        async with app.run_test():
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(total_cells=10, completed=1)

            def boom(_state: FooterState) -> str:
                raise ValueError("formatting exploded")

            footer._format_footer = boom  # type: ignore[method-assign]

            with caplog.at_level(logging.DEBUG, logger="harness_evaluator.tui.widgets"):
                footer._tick()  # must not raise

            assert any(
                "footer render failed" in record.message for record in caplog.records
            )

    async def test_tick_logs_a_persistent_fault_only_once(self, caplog):
        """A fault that never clears must log once, not once per tick — the
        production interval is 1s, so per-tick logging would write one
        record/second into the log pane for the rest of the run once DEBUG
        is on."""
        app = _FooterHostApp()
        async with app.run_test():
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(total_cells=10, completed=1)

            def boom(_state: FooterState) -> str:
                raise ValueError("formatting exploded")

            footer._format_footer = boom  # type: ignore[method-assign]

            with caplog.at_level(logging.DEBUG, logger="harness_evaluator.tui.widgets"):
                for _ in range(5):
                    footer._tick()

            records = [r for r in caplog.records if "footer render failed" in r.message]
            assert len(records) == 1

    async def test_tick_logs_again_after_recovering_then_faulting_again(self, caplog):
        """The once-only latch must clear on a successful render, so a
        *second*, distinct fault after a recovery still gets its own
        breadcrumb rather than being silently absorbed forever."""
        app = _FooterHostApp()
        async with app.run_test():
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(total_cells=10, completed=1)

            def boom(_state: FooterState) -> str:
                raise ValueError("formatting exploded")

            def working(state: FooterState) -> str:
                return "fine"

            with caplog.at_level(logging.DEBUG, logger="harness_evaluator.tui.widgets"):
                footer._format_footer = boom  # type: ignore[method-assign]
                footer._tick()
                footer._format_footer = working  # type: ignore[method-assign]
                footer._tick()
                footer._format_footer = boom  # type: ignore[method-assign]
                footer._tick()

            records = [r for r in caplog.records if "footer render failed" in r.message]
            assert len(records) == 2

    async def test_on_resize_re_renders_immediately(self):
        """Narrowing the terminal must not wait for the next periodic tick
        (up to 1s later) before the overflow indicator reappears."""
        app = _FooterHostApp()
        async with app.run_test(size=(100, 24)) as pilot:
            footer = app.query_one(ProgressFooter)
            footer.state = FooterState(
                total_cells=100,
                running=len(_LONG_RUNNING_CELLS),
                current_cell=_LONG_RUNNING_CELLS[-1],
                running_cells=list(_LONG_RUNNING_CELLS),
            )
            await pilot.pause()

            await pilot.resize_terminal(60, 24)
            # Deliberately no pilot.pause()/manual _tick() call: on_resize
            # itself must be what re-renders.
            rendered = str(footer._static.content)
            width = footer.content_size.width
            for line in rendered.split("\n"):
                assert len(line) <= width, (
                    f"line {line!r} exceeds content width {width} after resize "
                    "with no explicit re-render"
                )
