"""End-to-end rendering tests for the TUI footer's per-cell phase labels.

``tests/tui/test_footer_activity.py`` covers the *string* the footer builds
from a hand-built ``FooterState``. That was not enough: the phase label is
emitted in square brackets, and the markup-enabled ``Static`` that displayed
it parsed the label as a style tag and dropped it, so it never reached the
screen while every string-level assertion kept passing.

These tests drive the real ``EvalApp`` and its polling callback against a
real ``ResultsStore``, and assert on what is actually painted.
"""

from __future__ import annotations

import html
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.orchestrator.results_store import ResultsStore
from harness_evaluator.tui.app import EvalApp
from harness_evaluator.tui.widgets import FooterState, ProgressFooter

RUN_NAME = "footer-wiring"


def _cell() -> RunCell:
    return RunCell(
        run_name=RUN_NAME,
        harness=HarnessSpec(name="h", adapter="opencode"),
        model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
        task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
        repeat=0,
    )


@pytest.fixture
def store(tmp_path) -> ResultsStore:
    return ResultsStore(str(tmp_path / "results.db"))


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.name = RUN_NAME
    cfg.budget_usd = None
    return cfg


class _SeededApp(EvalApp):
    """EvalApp whose worker only wires the footer, then seeds one state.

    Mirrors what the real ``_run_eval`` does before awaiting the
    orchestrator, without running a matrix.
    """

    seed_state: FooterState

    def _run_eval(self) -> None:  # type: ignore[override]
        footer = self.query_one(ProgressFooter)
        footer._tick_callback = self._poll_cell_activity
        footer.state = self.seed_state


def _screen_text(app) -> str:
    """Return everything actually painted on screen, as one string."""
    svg = app.export_screenshot()
    return " ".join(
        html.unescape(t).replace("\xa0", " ")
        for t in re.findall(r"<text[^>]*>(.*?)</text>", svg)
    )


async def _tick(app, pilot) -> ProgressFooter:
    """Run one footer tick deterministically and let the screen repaint.

    Calls ``_tick`` directly rather than waiting on the 1s refresh timer,
    so the tests neither depend on wall-clock timing nor slow the suite.
    """
    await pilot.pause()
    footer = app.query_one(ProgressFooter)
    footer._tick()
    await pilot.pause()
    return footer


class TestPhaseLabelReachesTheScreen:
    async def test_phase_label_is_painted_not_eaten_as_markup(
        self, store, config
    ) -> None:
        cell = _cell()
        store.set_cell_state(cell.cell_id, RUN_NAME, "running")
        store.set_cell_phase(cell.cell_id, RUN_NAME, "harness_running")

        app = _SeededApp(config, store, AsyncMock(), [cell])
        app.seed_state = FooterState(
            total_cells=1,
            running=1,
            running_cells=[cell.cell_id],
            current_cell=cell.cell_id,
        )
        async with app.run_test() as pilot:
            footer = await _tick(app, pilot)
            assert footer.state.cell_phases == {cell.cell_id: "harness_running"}
            assert "[harness_running]" in footer._format_footer(footer.state)
            assert "[harness_running]" in _screen_text(app)

    async def test_phase_of_a_finished_cell_is_cleared(self, store, config) -> None:
        """Once nothing is running, ``current_cell`` still names the last
        cell (the orchestrator never clears it); its phase must not linger."""
        cell = _cell()
        app = _SeededApp(config, store, AsyncMock(), [cell])
        app.seed_state = FooterState(
            total_cells=1,
            completed=1,
            running=0,
            running_cells=[],
            current_cell=cell.cell_id,
            cell_phases={cell.cell_id: "harness_running"},
        )
        async with app.run_test() as pilot:
            footer = await _tick(app, pilot)
            assert footer.state.cell_phases == {}
            assert "[harness_running]" not in _screen_text(app)
