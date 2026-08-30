"""Reconciliation: compare token usage from different sources.

Sources:
  1. Proxy-captured usage (from SSE/JSON parsing)
  2. Provider billing API (if available)
  3. Harness self-report (if available)

Reconciliation is treated as classification, not arithmetic:
  - Within tolerance band → "reconciled"
  - Outside tolerance band → "discrepancy" (flagged as transparency metric)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from harness_evaluator.gateway.models import TokenUsage


class ReconciliationStatus(StrEnum):
    RECONCILED = "reconciled"
    DISCREPANCY = "discrepancy"
    SINGLE_SOURCE = "single_source"
    NO_DATA = "no_data"


@dataclass
class ReconciliationResult:
    """Result of reconciling token usage from multiple sources."""

    status: ReconciliationStatus
    primary_source: str  # "proxy", "billing", "self_report"
    proxy_usage: TokenUsage | None = None
    billing_usage: TokenUsage | None = None
    self_report_usage: TokenUsage | None = None
    discrepancies: dict[str, float] = field(default_factory=dict)
    """Field name → percentage difference (proxy vs other source)."""

    @property
    def best_estimate(self) -> TokenUsage | None:
        """Return the highest-confidence usage estimate."""
        # Priority: proxy > billing > self_report
        if self.proxy_usage:
            return self.proxy_usage
        if self.billing_usage:
            return self.billing_usage
        return self.self_report_usage


def reconcile_usage(
    proxy_usage: TokenUsage | None = None,
    billing_usage: TokenUsage | None = None,
    self_report_usage: TokenUsage | None = None,
    tolerance_pct: float = 2.0,
) -> ReconciliationResult:
    """Reconcile token usage from multiple sources.

    Args:
        proxy_usage: Usage captured by the gateway proxy.
        billing_usage: Usage from the provider's billing API.
        self_report_usage: Usage self-reported by the harness.
        tolerance_pct: Allowed percentage difference before flagging discrepancy.

    Returns:
        ReconciliationResult with status and per-field discrepancies.
    """
    sources = {
        "proxy": proxy_usage,
        "billing": billing_usage,
        "self_report": self_report_usage,
    }
    available = {k: v for k, v in sources.items() if v is not None}

    if not available:
        return ReconciliationResult(
            status=ReconciliationStatus.NO_DATA,
            primary_source="none",
        )

    if len(available) == 1:
        source_name = next(iter(available))
        return ReconciliationResult(
            status=ReconciliationStatus.SINGLE_SOURCE,
            primary_source=source_name,
            proxy_usage=proxy_usage,
            billing_usage=billing_usage,
            self_report_usage=self_report_usage,
        )

    # Compare proxy against other sources
    discrepancies: dict[str, float] = {}
    primary = "proxy" if proxy_usage else "billing" if billing_usage else "self_report"
    primary_usage = available[primary]

    for source_name, source_usage in available.items():
        if source_name == primary:
            continue
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        ):
            primary_val = getattr(primary_usage, field_name)
            source_val = getattr(source_usage, field_name)
            if primary_val == 0 and source_val == 0:
                continue
            if primary_val == 0 and source_val > 0:
                discrepancies[f"{source_name}.{field_name}"] = 100.0
                continue
            pct = abs(primary_val - source_val) / max(primary_val, 1) * 100
            if pct > tolerance_pct:
                discrepancies[f"{source_name}.{field_name}"] = pct

    status = (
        ReconciliationStatus.DISCREPANCY
        if discrepancies
        else ReconciliationStatus.RECONCILED
    )

    return ReconciliationResult(
        status=status,
        primary_source=primary,
        proxy_usage=proxy_usage,
        billing_usage=billing_usage,
        self_report_usage=self_report_usage,
        discrepancies=discrepancies,
    )
