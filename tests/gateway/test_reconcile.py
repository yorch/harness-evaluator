"""Tests for the reconciliation logic."""

from __future__ import annotations

from harnessbench.gateway.models import TokenUsage
from harnessbench.gateway.reconcile import (
    ReconciliationStatus,
    reconcile_usage,
)


class TestReconcileUsage:
    def test_no_data(self):
        result = reconcile_usage()
        assert result.status == ReconciliationStatus.NO_DATA
        assert result.best_estimate is None

    def test_single_source_proxy(self):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        result = reconcile_usage(proxy_usage=proxy)
        assert result.status == ReconciliationStatus.SINGLE_SOURCE
        assert result.primary_source == "proxy"
        assert result.best_estimate == proxy

    def test_reconciled_within_tolerance(self):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        billing = TokenUsage(input_tokens=101, output_tokens=50)
        result = reconcile_usage(
            proxy_usage=proxy,
            billing_usage=billing,
            tolerance_pct=2.0,
        )
        assert result.status == ReconciliationStatus.RECONCILED
        assert len(result.discrepancies) == 0

    def test_discrepancy_outside_tolerance(self):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        billing = TokenUsage(input_tokens=120, output_tokens=50)
        result = reconcile_usage(
            proxy_usage=proxy,
            billing_usage=billing,
            tolerance_pct=2.0,
        )
        assert result.status == ReconciliationStatus.DISCREPANCY
        assert "billing.input_tokens" in result.discrepancies
        assert result.discrepancies["billing.input_tokens"] > 2.0

    def test_three_sources_reconciled(self):
        proxy = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=10)
        billing = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=10)
        self_report = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=10)
        result = reconcile_usage(
            proxy_usage=proxy,
            billing_usage=billing,
            self_report_usage=self_report,
        )
        assert result.status == ReconciliationStatus.RECONCILED

    def test_three_sources_discrepancy(self):
        proxy = TokenUsage(input_tokens=100, output_tokens=50)
        billing = TokenUsage(input_tokens=100, output_tokens=50)
        self_report = TokenUsage(input_tokens=100, output_tokens=80)  # disagrees
        result = reconcile_usage(
            proxy_usage=proxy,
            billing_usage=billing,
            self_report_usage=self_report,
        )
        assert result.status == ReconciliationStatus.DISCREPANCY
        assert "self_report.output_tokens" in result.discrepancies

    def test_zero_values_not_flagged(self):
        """Zero vs zero should not be flagged as discrepancy."""
        proxy = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=0)
        billing = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=0)
        result = reconcile_usage(
            proxy_usage=proxy,
            billing_usage=billing,
        )
        assert result.status == ReconciliationStatus.RECONCILED
