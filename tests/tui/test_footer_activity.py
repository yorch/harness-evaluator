"""Tests for footer rendering with per-cell phase and API stats."""

from __future__ import annotations

from harness_evaluator.tui.widgets import FooterState, ProgressFooter


def _make_state(**kwargs) -> FooterState:
    """Build a FooterState with sensible defaults for activity tests."""
    defaults: dict[str, object] = {
        "total_cells": 10,
        "completed": 3,
        "failed": 1,
        "skipped": 0,
        "running": 1,
        "total_cost": 0.5,
        "current_cell": "h1__m1__t1__r0",
        "running_cells": ["h1__m1__t1__r0"],
    }
    defaults.update(kwargs)
    return FooterState(**defaults)  # type: ignore[arg-type]


class TestFooterPhaseRendering:
    def test_phase_shown_in_running_cell_line(self) -> None:
        state = _make_state(
            cell_phases={"h1__m1__t1__r0": "harness_running"},
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "[harness_running]" in rendered

    def test_no_phase_no_bracket(self) -> None:
        state = _make_state()
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "[harness_running]" not in rendered

    def test_multi_phase_label_shown(self) -> None:
        state = _make_state(
            cell_phases={"h1__m1__t1__r0": "harness_running:review"},
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "[harness_running:review]" in rendered

    def test_container_liveness_annotation(self) -> None:
        state = _make_state(
            cell_phases={"h1__m1__t1__r0": "harness_running:container_exited"},
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "[harness_running:container_exited]" in rendered


class TestFooterApiStatsRendering:
    def test_api_stats_shown(self) -> None:
        state = _make_state(
            cell_api_stats={"h1__m1__t1__r0": (5, 0.1234)},
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "5 calls" in rendered
        assert "$0.1234" in rendered

    def test_no_api_stats_no_suffix(self) -> None:
        state = _make_state()
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "calls" not in rendered

    def test_phase_and_api_stats_together(self) -> None:
        state = _make_state(
            cell_phases={"h1__m1__t1__r0": "harness_running"},
            cell_api_stats={"h1__m1__t1__r0": (3, 0.05)},
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "[harness_running]" in rendered
        assert "3 calls" in rendered


class TestFooterMultipleRunningCells:
    def test_phases_for_multiple_cells(self) -> None:
        state = _make_state(
            running=2,
            running_cells=["cell_a", "cell_b"],
            cell_phases={
                "cell_a": "harness_running",
                "cell_b": "evaluating",
            },
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "[harness_running]" in rendered
        assert "[evaluating]" in rendered

    def test_phase_only_for_shown_cells(self) -> None:
        """If there are more running cells than _MAX_RUNNING_CELLS_SHOWN,
        phases are only shown for the displayed cells."""
        from harness_evaluator.tui.widgets import _MAX_RUNNING_CELLS_SHOWN

        cells = [f"cell_{i}" for i in range(_MAX_RUNNING_CELLS_SHOWN + 2)]
        phases = dict.fromkeys(cells, "harness_running")
        state = _make_state(
            running=len(cells),
            running_cells=cells,
            cell_phases=phases,
        )
        footer = ProgressFooter()
        rendered = footer._format_footer(state)
        assert "… and 2 more" in rendered
