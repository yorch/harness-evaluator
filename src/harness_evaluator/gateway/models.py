"""Data models for token usage, cost, and captured requests."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_CHATGPT = "openai_chatgpt"


class ObservabilityTier(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    MINIMAL = "minimal"


class TokenUsage(BaseModel):
    """Token usage captured from a single provider API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.reasoning_tokens
        )


class CostBreakdown(BaseModel):
    """Cost in USD for a single API call."""

    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    reasoning_cost: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.input_cost
            + self.output_cost
            + self.cache_read_cost
            + self.cache_write_cost
            + self.reasoning_cost
        )


class CapturedCall(BaseModel):
    """A single provider API call captured by the gateway proxy."""

    id: str
    trace_id: str | None = None
    parent_id: str | None = None
    provider: Provider
    model: str
    method: str  # e.g. "POST"
    path: str  # e.g. "/v1/messages"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: dict[str, Any] | None = None
    response_status: int = 0
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: dict[str, Any] | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_streaming: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        return 200 <= self.response_status < 300 and self.error is None


class PricingTable(BaseModel):
    """Per-token pricing in USD per 1M tokens."""

    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cache_read_per_million: float = 0.0
    cache_write_per_million: float = 0.0
    reasoning_per_million: float = 0.0

    def calculate(self, usage: TokenUsage) -> CostBreakdown:
        m = 1_000_000.0
        return CostBreakdown(
            input_cost=usage.input_tokens * self.input_per_million / m,
            output_cost=usage.output_tokens * self.output_per_million / m,
            cache_read_cost=usage.cache_read_tokens * self.cache_read_per_million / m,
            cache_write_cost=usage.cache_write_tokens * self.cache_write_per_million / m,
            reasoning_cost=usage.reasoning_tokens * self.reasoning_per_million / m,
        )


# Default pricing per provider/model (USD per 1M tokens).
# These are defaults and can be overridden in config.
# Sources: https://www.anthropic.com/pricing, https://openai.com/api/pricing/
DEFAULT_PRICING: dict[str, PricingTable] = {
    # --- Anthropic ---
    "claude-sonnet-4-20250514": PricingTable(
        input_per_million=3.0,
        output_per_million=15.0,
        cache_read_per_million=0.30,
        cache_write_per_million=3.75,
    ),
    "claude-opus-4-20250514": PricingTable(
        input_per_million=15.0,
        output_per_million=75.0,
        cache_read_per_million=1.50,
        cache_write_per_million=18.75,
    ),
    "claude-haiku-3-5-20241022": PricingTable(
        input_per_million=0.80,
        output_per_million=4.0,
        cache_read_per_million=0.08,
        cache_write_per_million=1.0,
    ),
    "claude-3-5-sonnet-20241022": PricingTable(
        input_per_million=3.0,
        output_per_million=15.0,
        cache_read_per_million=0.30,
        cache_write_per_million=3.75,
    ),
    "claude-3-opus-20240229": PricingTable(
        input_per_million=15.0,
        output_per_million=75.0,
        cache_read_per_million=1.50,
        cache_write_per_million=18.75,
    ),
    "claude-3-haiku-20240307": PricingTable(
        input_per_million=0.25,
        output_per_million=1.25,
        cache_read_per_million=0.03,
        cache_write_per_million=0.30,
    ),
    # --- OpenAI ---
    "gpt-4o": PricingTable(
        input_per_million=2.50,
        output_per_million=10.0,
        cache_read_per_million=1.25,
    ),
    "gpt-4o-mini": PricingTable(
        input_per_million=0.15,
        output_per_million=0.60,
        cache_read_per_million=0.075,
    ),
    "o1": PricingTable(
        input_per_million=15.0,
        output_per_million=60.0,
        cache_read_per_million=7.50,
        reasoning_per_million=60.0,
    ),
    "o1-mini": PricingTable(
        input_per_million=1.10,
        output_per_million=4.40,
        cache_read_per_million=0.55,
        reasoning_per_million=4.40,
    ),
    "o1-pro": PricingTable(
        input_per_million=150.0,
        output_per_million=600.0,
    ),
    "o3": PricingTable(
        input_per_million=2.0,
        output_per_million=8.0,
        cache_read_per_million=0.50,
    ),
    "o3-mini": PricingTable(
        input_per_million=1.10,
        output_per_million=4.40,
        cache_read_per_million=0.55,
        reasoning_per_million=4.40,
    ),
    "gpt-4-turbo": PricingTable(
        input_per_million=10.0,
        output_per_million=30.0,
    ),
    "gpt-4": PricingTable(
        input_per_million=30.0,
        output_per_million=60.0,
    ),
    "gpt-3.5-turbo": PricingTable(
        input_per_million=0.50,
        output_per_million=1.50,
    ),
}


def get_pricing(model: str) -> PricingTable:
    """Get pricing for a model, falling back to a zero-cost default.

    .. deprecated::
       Unknown models silently receive a zero-cost PricingTable, which
       means their token usage will not count against the budget. Call
       :func:`get_pricing_strict` to get a clear signal (warning log)
       when a model is not in the pricing table.
    """
    return DEFAULT_PRICING.get(model, PricingTable())


def get_pricing_strict(model: str) -> PricingTable:
    """Get pricing for a model, warning on unknown models.

    Logs a hard warning when the model is not in the pricing table,
    since a zero-cost fallback means token usage will not count against
    the budget — a silent budget bypass.
    """
    import logging

    pricing = DEFAULT_PRICING.get(model)
    if pricing is None:
        logging.getLogger(__name__).warning(
            "No pricing found for model '%s'; cost will be $0 and "
            "token usage will NOT count against the budget. "
            "Add the model to DEFAULT_PRICING to fix this.",
            model,
        )
        return PricingTable()
    return pricing
