"""Tests for the CellOutputPanel widget."""

from __future__ import annotations

from harness_evaluator.tui.widgets import MAX_CELL_OUTPUT_LINES, CellOutputPanel


class TestCellOutputPanelFeed:
    def test_feed_complete_line(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stdout", b"hello world\n")
        assert panel._cell_lines.get("cell1") == ["hello world"]

    def test_feed_partial_line_buffered(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stdout", b"hello wor")
        assert panel._cell_lines.get("cell1") == []
        panel.feed("cell1", "stdout", b"ld\n")
        assert panel._cell_lines.get("cell1") == ["hello world"]

    def test_feed_stderr_prefix(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stderr", b"error msg\n")
        assert panel._cell_lines.get("cell1") == ["[stderr] error msg"]

    def test_feed_redacts_secrets(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stdout", b"key=sk-ant-abcdefghij\n")
        lines = panel._cell_lines.get("cell1", [])
        assert len(lines) == 1
        assert "sk-ant-abcdefghij" not in lines[0]
        assert "[REDACTED]" in lines[0]

    def test_feed_multiple_cells_isolated(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stdout", b"cell1 output\n")
        panel.feed("cell2", "stdout", b"cell2 output\n")
        assert panel._cell_lines["cell1"] == ["cell1 output"]
        assert panel._cell_lines["cell2"] == ["cell2 output"]

    def test_feed_bounded_retention(self) -> None:
        panel = CellOutputPanel()
        # Feed more lines than the retention limit
        for i in range(MAX_CELL_OUTPUT_LINES + 50):
            panel.feed("cell1", "stdout", f"line {i}\n".encode())
        lines = panel._cell_lines["cell1"]
        assert len(lines) == MAX_CELL_OUTPUT_LINES
        # Should keep the tail (most recent)
        assert lines[-1] == f"line {MAX_CELL_OUTPUT_LINES + 49}"
        assert lines[0] == f"line {50}"

    def test_flush_cell(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stdout", b"line1\npartial")
        panel.flush_cell("cell1")
        lines = panel._cell_lines["cell1"]
        assert "partial" in lines[-1]

    def test_clear_cell(self) -> None:
        panel = CellOutputPanel()
        panel.feed("cell1", "stdout", b"output\n")
        panel.clear_cell("cell1")
        assert "cell1" not in panel._cell_lines
        assert "cell1" not in panel._redactors


class TestCellOutputPanelSelection:
    def test_select_cell_shows_panel(self) -> None:
        panel = CellOutputPanel()
        panel.select_cell("cell1")
        assert panel._selected_cell == "cell1"

    def test_select_none_hides_panel(self) -> None:
        panel = CellOutputPanel()
        panel.select_cell("cell1")
        panel.select_cell(None)
        assert panel._selected_cell is None

    def test_cycle_cell_through_running(self) -> None:
        panel = CellOutputPanel()
        running = ["cell1", "cell2", "cell3"]
        panel.cycle_cell(running)
        assert panel._selected_cell == "cell1"
        panel.cycle_cell(running)
        assert panel._selected_cell == "cell2"
        panel.cycle_cell(running)
        assert panel._selected_cell == "cell3"
        panel.cycle_cell(running)
        assert panel._selected_cell == "cell1"

    def test_cycle_cell_empty_hides(self) -> None:
        panel = CellOutputPanel()
        panel.select_cell("cell1")
        panel.cycle_cell([])
        assert panel._selected_cell is None

    def test_cycle_cell_skips_completed(self) -> None:
        panel = CellOutputPanel()
        panel.select_cell("cell1")
        # cell1 is no longer running
        panel.cycle_cell(["cell2"])
        assert panel._selected_cell == "cell2"
