"""Tests for the reconciliation_results table in ResultsStore."""

from __future__ import annotations

import json

import pytest

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.results_store import ResultsStore


@pytest.fixture
def store(tmp_path):
    return ResultsStore(str(tmp_path / "test_results.db"))


class TestReconciliationStore:
    def test_save_and_retrieve_matched(self, store):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        self_reported = TokenUsage(input_tokens=101, output_tokens=50)
        store.save_reconciliation_result(
            cell_id="cell-1",
            run_name="run-1",
            proxy_usage=proxy,
            self_reported_usage=self_reported,
            matched=True,
            max_discrepancy_pct=1.0,
            details={"self_report.input_tokens": 1.0},
        )

        result = store.get_reconciliation_result("cell-1")
        assert result is not None
        assert result["cell_id"] == "cell-1"
        assert result["run_name"] == "run-1"
        assert result["matched"] == 1
        assert result["max_discrepancy_pct"] == pytest.approx(1.0)

        proxy_parsed = json.loads(result["proxy_usage_json"])
        assert proxy_parsed["input_tokens"] == 100
        assert proxy_parsed["output_tokens"] == 50

        sr_parsed = json.loads(result["self_reported_usage_json"])
        assert sr_parsed["input_tokens"] == 101

        details = json.loads(result["details_json"])
        assert "self_report.input_tokens" in details

    def test_save_and_retrieve_discrepancy(self, store):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        self_reported = TokenUsage(input_tokens=150, output_tokens=50)
        store.save_reconciliation_result(
            cell_id="cell-2",
            run_name="run-1",
            proxy_usage=proxy,
            self_reported_usage=self_reported,
            matched=False,
            max_discrepancy_pct=50.0,
            details={"self_report.input_tokens": 50.0},
        )

        result = store.get_reconciliation_result("cell-2")
        assert result is not None
        assert result["matched"] == 0
        assert result["max_discrepancy_pct"] == pytest.approx(50.0)

    def test_save_with_null_self_reported(self, store):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        store.save_reconciliation_result(
            cell_id="cell-3",
            run_name="run-1",
            proxy_usage=proxy,
            self_reported_usage=None,
            matched=True,
            max_discrepancy_pct=0.0,
            details=None,
        )

        result = store.get_reconciliation_result("cell-3")
        assert result is not None
        assert result["self_reported_usage_json"] is None
        assert result["details_json"] is None

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_reconciliation_result("no-such-cell") is None

    def test_save_replaces_existing(self, store):
        """Saving twice for the same cell_id replaces the prior result."""
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        store.save_reconciliation_result(
            cell_id="cell-4",
            run_name="run-1",
            proxy_usage=proxy,
            self_reported_usage=TokenUsage(input_tokens=100, output_tokens=50),
            matched=True,
            max_discrepancy_pct=0.0,
            details={},
        )
        store.save_reconciliation_result(
            cell_id="cell-4",
            run_name="run-1",
            proxy_usage=proxy,
            self_reported_usage=TokenUsage(input_tokens=200, output_tokens=50),
            matched=False,
            max_discrepancy_pct=100.0,
            details={"self_report.input_tokens": 100.0},
        )

        result = store.get_reconciliation_result("cell-4")
        assert result is not None
        assert result["matched"] == 0
        assert result["max_discrepancy_pct"] == pytest.approx(100.0)
