"""Logging handler that bridges log records into a Textual RichLog widget."""

from __future__ import annotations

import logging
from logging import Formatter, LogRecord
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from textual.app import App
    from textual.widgets import RichLog

# Map logging levels to Rich style names.
_LEVEL_STYLES: dict[int, str] = {
    logging.DEBUG: "dim",
    logging.INFO: "",
    logging.WARNING: "yellow",
    logging.ERROR: "bold red",
    logging.CRITICAL: "bold red on black",
}

# Short level names for the log line prefix.
_LEVEL_NAMES: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRIT",
}


class TuiLogHandler(logging.Handler):
    """Logging handler that posts formatted lines to a Textual RichLog widget.

    Thread-safe: uses ``app.call_from_thread`` to schedule the widget write
    on the Textual event loop, so logs from aiohttp worker threads or other
    non-asyncio threads are safe.
    """

    def __init__(self, app: App[object], log_widget: RichLog) -> None:
        super().__init__()
        self._app = app
        self._log_widget = log_widget
        self._show_time = True

    def set_show_time(self, show: bool) -> None:
        self._show_time = show

    def emit(self, record: LogRecord) -> None:
        try:
            text = self._format_record(record)
            # call_from_thread is safe from any thread (including the main
            # event loop thread — it just schedules a callback).
            self._app.call_from_thread(self._log_widget.write, text)
        except Exception:
            # Never let logging raise — it can crash the orchestrator.
            self.handleError(record)

    def _format_record(self, record: LogRecord) -> Text:
        """Format a log record as a Rich Text line."""
        style = _LEVEL_STYLES.get(record.levelno, "")
        level_name = _LEVEL_NAMES.get(record.levelno, record.levelname)

        parts: list[tuple[str, str]] = []
        if self._show_time:
            ts = Formatter().formatTime(record, "%H:%M:%S")
            parts.append((f"{ts} ", "dim cyan"))
        parts.append((f"{level_name:<5} ", style or "bold"))
        parts.append((record.getMessage(), style))

        text = Text.assemble(*parts)
        return text
