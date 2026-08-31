"""Tests for the TUI progress footer widget."""

from __future__ import annotations

import time

from harness_evaluator.tui.widgets import FooterState, ProgressFooter, _format_cell_id


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
