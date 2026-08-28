"""Data models for token usage, cost, and captured requests."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


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
DEFAULT_PRICING: dict[str, PricingTable] = {
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
    "gpt-4o": PricingTable(
        input_per_million=2.50,
        output_per_million=10.0,
    ),
    "gpt-4o-mini": PricingTable(
        input_per_million=0.15,
        output_per_million=0.60,
    ),
    "o1": PricingTable(
        input_per_million=15.0,
        output_per_million=60.0,
        reasoning_per_million=60.0,
    ),
    "o3-mini": PricingTable(
        input_per_million=3.0,
        output_per_million=12.0,
        reasoning_per_million=12.0,
    ),
}


def get_pricing(model: str) -> PricingTable:
    """Get pricing for a model, falling back to a zero-cost default."""
    return DEFAULT_PRICING.get(model, PricingTable())
