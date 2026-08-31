"""Tests for the TUI EvalApp."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness_evaluator.tui.app import EvalApp
from harness_evaluator.tui.widgets import FooterState


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
