"""Tests for the TUI EvalApp."""

from __future__ import annotations

import html
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness_evaluator.tui.app import EvalApp
from harness_evaluator.tui.log_handler import TuiLogHandler
from harness_evaluator.tui.widgets import FooterBar, ProgressFooter


def _rendered_texts(app) -> list[str]:
    """Extract the plain text of every rendered glyph run from a real
    App.run_test() screenshot, for assertions against what actually
    appears on screen (not just what a string-building helper produced)."""
    svg = app.export_screenshot()
    raw = re.findall(r"<text[^>]*>(.*?)</text>", svg)
    return [html.unescape(t).replace("\xa0", " ") for t in raw]


class _NoOpEvalApp(EvalApp):
    """EvalApp with the orchestrator worker stubbed out.

    ``on_mount`` starts ``_run_eval`` as a background worker that constructs
    a real ``Orchestrator`` and drives an eval run. Widget-lifecycle tests
    only need mount/unmount and the footer/logging setup that happens around
    it, so this override replaces that worker with a no-op.
    """

    def _run_eval(self) -> None:  # type: ignore[override]
        return None


@pytest.fixture
def mock_config():
    """Minimal RunConfig-like mock."""
    cfg = MagicMock()
    cfg.name = "test-run"
    cfg.budget_usd = None
    return cfg


@pytest.fixture
def mock_store():
    """Minimal ResultsStore mock."""
    return MagicMock()


@pytest.fixture
def mock_cells():
    """Minimal list of cell-like mocks."""
    cell = MagicMock()
    cell.cell_id = "h1__m1__t1__r0"
    return [cell]


@pytest.fixture
def isolated_logging():
    """Snapshot and restore root + harness_evaluator logger state.

    EvalApp.on_mount/on_unmount mutate global logging state; tests that
    exercise that lifecycle must not leak handlers/levels into the rest of
    the suite.
    """
    root = logging.getLogger()
    package_logger = logging.getLogger("harness_evaluator")
    prev_root_handlers = list(root.handlers)
    prev_root_level = root.level
    prev_package_level = package_logger.level
    try:
        yield root, package_logger
    finally:
        root.handlers = prev_root_handlers
        root.setLevel(prev_root_level)
        package_logger.setLevel(prev_package_level)


class TestEvalApp:
    """Tests for the EvalApp."""

    def test_app_creation(self, mock_config, mock_store, mock_cells):
        """EvalApp can be created with config, store, runner, cells."""
        run_cell_fn = AsyncMock()
        app = EvalApp(mock_config, mock_store, run_cell_fn, mock_cells)
        assert app._config == mock_config
        assert app._store == mock_store
        assert app._run_cell_fn == run_cell_fn
        assert app._cells == mock_cells
        assert app._result is None

    def test_result_property_initially_none(self, mock_config, mock_store, mock_cells):
        """result is None before the app runs."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        assert app.result is None

    def test_bindings_include_quit_and_toggles(self, mock_config, mock_store, mock_cells):
        """App has the expected key bindings."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        binding_keys = {b[0] for b in app.BINDINGS}
        assert "q" in binding_keys
        assert "d" in binding_keys
        assert "t" in binding_keys
        assert "f" in binding_keys

    def test_base_level_is_info(self, mock_config, mock_store, mock_cells):
        """Default log level is INFO."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        assert app._base_level == logging.INFO

    def test_show_timestamps_default_true(self, mock_config, mock_store, mock_cells):
        """Timestamps are shown by default."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        assert app._show_timestamps is True

    def test_auto_follow_default_true(self, mock_config, mock_store, mock_cells):
        """Auto-follow is enabled by default."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        assert app._auto_follow is True

    def test_action_toggle_debug_switches_level(self, mock_config, mock_store, mock_cells):
        """Toggling debug switches between INFO and DEBUG."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        mock_log = MagicMock()
        app.query_one = MagicMock(return_value=mock_log)
        assert app._base_level == logging.INFO
        app.action_toggle_debug()
        assert app._base_level == logging.DEBUG
        app.action_toggle_debug()
        assert app._base_level == logging.INFO

    def test_action_toggle_timestamps(self, mock_config, mock_store, mock_cells):
        """Toggling timestamps flips the flag."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        mock_log = MagicMock()
        app.query_one = MagicMock(return_value=mock_log)
        assert app._show_timestamps is True
        app.action_toggle_timestamps()
        assert app._show_timestamps is False
        app.action_toggle_timestamps()
        assert app._show_timestamps is True

    def test_action_toggle_follow(self, mock_config, mock_store, mock_cells):
        """Toggling follow flips the flag."""
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        mock_log = MagicMock()
        app.query_one = MagicMock(return_value=mock_log)
        assert app._auto_follow is True
        app.action_toggle_follow()
        assert app._auto_follow is False
        app.action_toggle_follow()
        assert app._auto_follow is True


class TestEvalAppVerbose:
    """The `verbose` constructor kwarg picks the TUI's initial log level."""

    def test_verbose_default_is_zero_and_info(self, mock_config, mock_store, mock_cells):
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        assert app._verbose == 0
        assert app._base_level == logging.INFO

    def test_verbose_one_is_still_info_floor(self, mock_config, mock_store, mock_cells):
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells, verbose=1)
        assert app._verbose == 1
        assert app._base_level == logging.INFO

    def test_verbose_two_is_debug(self, mock_config, mock_store, mock_cells):
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells, verbose=2)
        assert app._verbose == 2
        assert app._base_level == logging.DEBUG

    def test_verbose_above_two_is_still_debug(self, mock_config, mock_store, mock_cells):
        app = EvalApp(mock_config, mock_store, AsyncMock(), mock_cells, verbose=5)
        assert app._base_level == logging.DEBUG


class TestEvalAppComposeAndBindings:
    """F10: key bindings must be discoverable via a real Footer widget."""

    async def test_footer_widget_is_mounted(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        from textual.widgets import Footer

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            # Raises if no Footer widget is present in the composed tree.
            app.query_one(Footer)

    async def test_bindings_render_with_their_configured_descriptions(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        """The bindings footer must actually display each binding's
        configured description text, in a real rendered screen.

        A test that only inspects BINDINGS tuples in isolation (asserting a
        description is non-empty and != the raw action name) is satisfied by
        the OLD one-word descriptions ("Quit", "Debug", "Timestamps",
        "Follow") too, so it can't tell the F10 wording improvement from a
        revert of it. Rendering the real Footer and reading back what it
        drew is the only thing that discriminates.
        """
        # Hardcoded expected text, not derived from app.BINDINGS: asserting
        # "whatever BINDINGS currently says gets rendered" is tautological
        # and passes even if BINDINGS reverts to the old one-word labels.
        expected_descriptions = {
            "q": "Quit (cancels the run)",
            "d": "Toggle debug logging",
            "t": "Toggle timestamps",
            "f": "Toggle auto-follow",
        }
        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            rendered = " ".join(_rendered_texts(app))
            for key, description in expected_descriptions.items():
                assert description in rendered, (
                    f"binding {key!r}'s expected description {description!r} was not "
                    f"found in the rendered Footer: {rendered!r}"
                )


class TestEvalAppProgressReactive:
    """F6/F14: progress snapshots must drive the footer's reactive state,
    with total_cells refreshed from each snapshot."""

    async def test_on_progress_fires_watch_state_and_refreshes_total_cells(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test() as pilot:
            footer = app.query_one(ProgressFooter)
            seen_completed: list[int] = []
            original_watch_state = footer.watch_state

            def spy_watch_state(state):
                seen_completed.append(state.completed)
                original_watch_state(state)

            footer.watch_state = spy_watch_state  # type: ignore[method-assign]

            # mock_cells has a single cell, so a snapshot reporting a
            # different total_cells must not be masked by the initial seed.
            snapshot_1 = SimpleNamespace(
                total_cells=42,
                completed=1,
                failed=0,
                skipped=0,
                running=1,
                total_cost=0.1,
                current_cell="c1",
                running_cells=["c1"],
            )
            snapshot_2 = SimpleNamespace(
                total_cells=42,
                completed=2,
                failed=0,
                skipped=0,
                running=0,
                total_cost=0.2,
                current_cell=None,
                running_cells=[],
            )

            app._on_progress(snapshot_1)  # type: ignore[arg-type]
            await pilot.pause()
            app._on_progress(snapshot_2)  # type: ignore[arg-type]
            await pilot.pause()

            assert seen_completed == [1, 2]
            assert footer.state.total_cells == 42
            assert footer.state.completed == 2


class TestEvalAppLoggingLifecycle:
    """F13/F15: the debug toggle is scoped to the harness_evaluator logger
    tree, and unmounting restores the logging state the TUI found."""

    async def test_debug_toggle_does_not_touch_root_level(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        root, package_logger = isolated_logging
        root.setLevel(logging.WARNING)
        root.handlers = []

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            assert root.level == logging.WARNING
            assert package_logger.level == logging.INFO

            app.action_toggle_debug()

            assert package_logger.level == logging.DEBUG
            assert root.level == logging.WARNING

    async def test_unmount_restores_previous_handlers_and_level(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        root, _package_logger = isolated_logging
        sentinel = logging.NullHandler()
        root.handlers = [sentinel]
        root.setLevel(logging.ERROR)

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            assert any(isinstance(h, TuiLogHandler) for h in root.handlers)
            assert sentinel not in root.handlers

        assert root.handlers == [sentinel]
        assert root.level == logging.ERROR

    async def test_unmount_installs_a_console_handler_when_none_existed(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        root, _package_logger = isolated_logging
        root.handlers = []

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            pass

        assert len(root.handlers) >= 1
        assert not any(isinstance(h, TuiLogHandler) for h in root.handlers)

    async def test_unmount_fallback_handler_has_a_formatter(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        """cli.py's `run` command never calls _configure_logging on the TUI
        path, so this fallback StreamHandler is what actually gets
        installed on the real CLI today — it must not write bare
        %(message)s with no level/logger/timestamp."""
        root, _package_logger = isolated_logging
        root.handlers = []

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            pass

        assert root.handlers[0].formatter is not None

    async def test_unmount_restore_does_not_alias_the_saved_handler_list(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        """The restored root.handlers must be a copy of the app's saved
        snapshot, not the same list object — otherwise a later
        root.addHandler() call would silently mutate the app's own saved
        state too."""
        root, _package_logger = isolated_logging
        sentinel = logging.NullHandler()
        root.handlers = [sentinel]
        root.setLevel(logging.ERROR)

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            pass

        assert root.handlers is not app._previous_root_handlers
        root.addHandler(logging.NullHandler())
        assert len(app._previous_root_handlers) == 1


class TestEvalAppLayout:
    """The bottom chrome (FooterBar: ProgressFooter + the bindings Footer)
    must not overlap itself, and must still leave the log some content
    rows at a normal terminal size."""

    async def test_log_has_content_rows_at_normal_size(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        """Assert on content_size, not region: region includes the 2-row
        RichLog border, so it stays > 0 even with zero usable rows inside —
        mutating _FOOTER_HEIGHT to starve the log to 0 content rows at
        80x24 left the whole tui suite green against the region-based
        assertion."""
        from textual.widgets import RichLog

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            log = app.query_one("#eval-log", RichLog)
            assert log.content_size.height > 0

    async def test_progress_footer_and_bindings_footer_do_not_overlap_at_short_height(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        """Two independent dock:bottom siblings (the pre-fix layout) overlap
        — verified directly against ProgressFooter/Footer regions, which is
        exactly the failure this container fixes. 80x10, not 80x12: the
        pre-fix two-sibling layout does not actually overlap at 80x12 (only
        at 80x10 and shorter), so 80x12 doesn't discriminate the fix."""
        from textual.widgets import Footer

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test(size=(80, 10)) as pilot:
            await pilot.pause()
            progress_footer = app.query_one(ProgressFooter)
            bindings_footer = app.query_one(Footer)
            assert not progress_footer.region.overlaps(bindings_footer.region)

    async def test_footer_bar_holds_both_footers_together(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        """Both footers live under a single FooterBar container (the
        mandated fix), not as separate top-level dock:bottom siblings."""
        from textual.widgets import Footer

        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            bar = app.query_one(FooterBar)
            assert isinstance(bar.query_one(ProgressFooter), ProgressFooter)
            assert isinstance(bar.query_one(Footer), Footer)


class TestEvalAppProgressAfterTeardown:
    """F6 worker callback safety: a late progress callback after the
    screen has torn down must not raise (Minor finding: query_one used to
    be unguarded)."""

    async def test_on_progress_after_app_exit_does_not_raise(
        self, mock_config, mock_store, mock_cells, isolated_logging
    ):
        app = _NoOpEvalApp(mock_config, mock_store, AsyncMock(), mock_cells)
        async with app.run_test():
            pass  # app is torn down on context exit

        snapshot = SimpleNamespace(
            total_cells=1,
            completed=1,
            failed=0,
            skipped=0,
            running=0,
            total_cost=0.0,
            current_cell=None,
            running_cells=[],
        )
        # Must not raise textual.css.query.NoMatches.
        app._on_progress(snapshot)  # type: ignore[arg-type]
