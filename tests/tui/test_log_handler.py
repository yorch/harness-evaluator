"""Tests for the TUI log handler."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from rich.text import Text

from harness_evaluator.tui.log_handler import TuiLogHandler


class TestTuiLogHandler:
    """Tests for the TUI log handler."""

    def _make_handler(self) -> tuple[TuiLogHandler, MagicMock]:
        """Create a handler with a mock app and log widget."""
        app = MagicMock()
        log_widget = MagicMock()
        handler = TuiLogHandler(app, log_widget)
        return handler, log_widget

    def test_emit_info_record(self):
        """INFO records are formatted and written to the widget."""
        handler, log_widget = self._make_handler()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Cell %s started",
            args=("abc",),
            exc_info=None,
        )
        handler.emit(record)
        # Same-thread calls write directly to the widget.
        log_widget.write.assert_called_once()
        text = log_widget.write.call_args.args[0]
        assert "Cell abc started" in text.plain

    def test_emit_debug_record(self):
        """DEBUG records are formatted with dim style."""
        handler, _ = self._make_handler()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="Debug message",
            args=(),
            exc_info=None,
        )
        text = handler._format_record(record)
        assert isinstance(text, Text)
        assert "Debug message" in text.plain
        assert "DEBUG" in text.plain

    def test_emit_warning_record(self):
        """WARNING records use the short WARN label."""
        handler, _ = self._make_handler()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="Something went wrong",
            args=(),
            exc_info=None,
        )
        text = handler._format_record(record)
        assert "WARN" in text.plain
        assert "Something went wrong" in text.plain

    def test_emit_error_record(self):
        """ERROR records are formatted with bold red style."""
        handler, _ = self._make_handler()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Cell failed",
            args=(),
            exc_info=None,
        )
        text = handler._format_record(record)
        assert "ERROR" in text.plain
        assert "Cell failed" in text.plain

    def test_timestamps_included_by_default(self):
        """Timestamps are shown by default."""
        handler, _ = self._make_handler()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello",
            args=(),
            exc_info=None,
        )
        text = handler._format_record(record)
        # Should contain a timestamp pattern HH:MM:SS
        assert ":" in text.plain
        # The message should be after the timestamp
        assert "Hello" in text.plain

    def test_timestamps_can_be_disabled(self):
        """Timestamps can be toggled off."""
        handler, _ = self._make_handler()
        handler.set_show_time(False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello",
            args=(),
            exc_info=None,
        )
        text = handler._format_record(record)
        # Should start with the level name, not a timestamp
        assert text.plain.startswith("INFO")

    def test_emit_does_not_raise_on_widget_error(self):
        """If the widget write fails, the handler should not raise."""
        app = MagicMock()
        log_widget = MagicMock()
        log_widget.write.side_effect = RuntimeError("widget gone")
        handler = TuiLogHandler(app, log_widget)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello",
            args=(),
            exc_info=None,
        )
        # Should not raise — handleError swallows it.
        handler.emit(record)

    def test_set_show_time_toggles_flag(self):
        """set_show_time updates the internal flag."""
        handler, _ = self._make_handler()
        assert handler._show_time is True
        handler.set_show_time(False)
        assert handler._show_time is False
        handler.set_show_time(True)
        assert handler._show_time is True

    def test_set_level_filters_records(self):
        """setLevel filters records below the threshold via a logger."""
        handler, log_widget = self._make_handler()
        handler.setLevel(logging.WARNING)

        logger = logging.getLogger("test_tui_filter")
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)

        logger.info("info message")
        log_widget.write.assert_not_called()

        logger.warning("warn message")
        log_widget.write.assert_called_once()
