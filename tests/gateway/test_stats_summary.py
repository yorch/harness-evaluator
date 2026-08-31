"""Tests for the periodic aggregated stats summary loop."""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import patch

import pytest

from harness_evaluator.gateway.proxy import (
    STATS_SUMMARY_INTERVAL,
    _stats_summary_loop,
)
from harness_evaluator.gateway.store import CallStore


@pytest.fixture
def store(tmp_db: str) -> CallStore:
    """Provide a CallStore backed by a temporary DB."""
    return CallStore(tmp_db)


class TestStatsSummaryLoop:
    """Tests for _stats_summary_loop."""

    async def test_loop_is_noop_when_info_disabled(self, store: CallStore) -> None:
        """The loop returns immediately when INFO logging is disabled."""
        with patch(
            "harness_evaluator.gateway.proxy.logger.isEnabledFor",
            return_value=False,
        ):
            start = time.monotonic()
            await _stats_summary_loop(store, time.monotonic())
            elapsed = time.monotonic() - start

        # Should return near-instantly without sleeping the full interval.
        assert elapsed < 1.0

    async def test_loop_emits_summary_with_expected_fields(
        self, store: CallStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The loop logs a summary containing all expected fields."""
        # Insert a captured call directly via the store so there is data.
        from harness_evaluator.gateway.models import (
            CapturedCall,
            CostBreakdown,
            Provider,
            TokenUsage,
        )

        call = CapturedCall(
            id="test-1",
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            method="POST",
            path="/v1/messages",
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            cost=CostBreakdown(input_cost=0.0003, output_cost=0.00075),
            latency_ms=1234.0,
        )
        store.save(call)

        # Patch the interval to a tiny value so the loop fires quickly.
        with (
            patch(
                "harness_evaluator.gateway.proxy.STATS_SUMMARY_INTERVAL",
                0.05,
            ),
            patch(
                "harness_evaluator.gateway.proxy.logger.isEnabledFor",
                return_value=True,
            ),
            caplog.at_level(logging.INFO, logger="harness_evaluator.gateway.proxy"),
        ):
            task = asyncio.create_task(_stats_summary_loop(store, time.monotonic()))
            # Allow at least one iteration to run.
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # Find the summary log record.
        summary_records = [
            r for r in caplog.records if "Gateway stats summary" in r.getMessage()
        ]
        assert len(summary_records) >= 1
        message = summary_records[0].getMessage()

        # Verify all expected fields are present.
        assert "Uptime" in message
        assert "Total calls captured" in message
        assert "Calls in last interval" in message
        assert "Total cost" in message
        assert "Total tokens" in message
        assert "input" in message
        assert "output" in message
        assert "Average latency" in message

        # Verify the actual aggregated values.
        assert "Total calls captured: 1" in message
        # 100 input + 50 output = 150 tokens.
        assert "Total tokens: 150" in message
        assert "input: 100" in message
        assert "output: 50" in message

    async def test_loop_handles_db_errors_gracefully(
        self, store: CallStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The loop logs a warning and continues when the DB query fails."""
        with (
            patch(
                "harness_evaluator.gateway.proxy.STATS_SUMMARY_INTERVAL",
                0.05,
            ),
            patch(
                "harness_evaluator.gateway.proxy.logger.isEnabledFor",
                return_value=True,
            ),
            patch.object(
                store,
                "get_stats_summary",
                side_effect=RuntimeError("DB locked"),
            ),
            caplog.at_level(logging.INFO, logger="harness_evaluator.gateway.proxy"),
        ):
            task = asyncio.create_task(_stats_summary_loop(store, time.monotonic()))
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # A warning should have been logged for the failed query.
        warnings = [
            r
            for r in caplog.records
            if "Stats summary query failed" in r.getMessage()
        ]
        assert len(warnings) >= 1
        assert warnings[0].levelno == logging.WARNING

    async def test_loop_interval_constant(self) -> None:
        """The default interval is 30 seconds."""
        assert STATS_SUMMARY_INTERVAL == 30.0


class TestStoreStatsSummary:
    """Tests for CallStore.get_stats_summary and get_calls_since."""

    def test_empty_store_summary(self, store: CallStore) -> None:
        """An empty store returns zeroed stats."""
        stats = store.get_stats_summary()
        assert stats["total_calls"] == 0
        assert stats["total_cost"] == 0.0
        assert stats["total_input_tokens"] == 0
        assert stats["total_output_tokens"] == 0
        assert stats["total_tokens"] == 0
        assert stats["avg_latency_ms"] == 0.0

    def test_summary_aggregates_multiple_calls(self, store: CallStore) -> None:
        """The summary aggregates cost, tokens, and latency across calls."""
        from harness_evaluator.gateway.models import (
            CapturedCall,
            CostBreakdown,
            Provider,
            TokenUsage,
        )

        for i in range(3):
            store.save(
                CapturedCall(
                    id=f"call-{i}",
                    provider=Provider.ANTHROPIC,
                    model="claude-sonnet-4-20250514",
                    method="POST",
                    path="/v1/messages",
                    usage=TokenUsage(input_tokens=100, output_tokens=50),
                    cost=CostBreakdown(input_cost=0.0003, output_cost=0.00075),
                    latency_ms=1000.0,
                )
            )

        stats = store.get_stats_summary()
        assert stats["total_calls"] == 3
        assert stats["total_input_tokens"] == 300
        assert stats["total_output_tokens"] == 150
        assert stats["total_tokens"] == 450
        assert abs(stats["total_cost"] - (3 * 0.00105)) < 1e-9
        assert stats["avg_latency_ms"] == 1000.0

    def test_get_calls_since_returns_recent_only(
        self, store: CallStore
    ) -> None:
        """get_calls_since counts calls after the given timestamp."""
        from datetime import UTC, datetime, timedelta

        from harness_evaluator.gateway.models import (
            CapturedCall,
            Provider,
        )

        old_call = CapturedCall(
            id="old",
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            method="POST",
            path="/v1/messages",
            timestamp=datetime.now(UTC) - timedelta(hours=1),
        )
        store.save(old_call)

        # A timestamp in the future should exclude the old call.
        future = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        assert store.get_calls_since(future) == 0

        # A timestamp in the past should include the old call.
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        assert store.get_calls_since(past) == 1

    def test_summary_handles_missing_json_fields(self, store: CallStore) -> None:
        """Rows with missing JSON fields are treated as zero, not NULL.

        Without per-field COALESCE, a row missing e.g. reasoning_cost
        would produce NULL for the entire sum, silently excluding that
        row from total_cost.
        """
        import sqlite3

        # Insert a row with partial cost_json (missing reasoning_cost,
        # cache_read_cost, etc.) and partial usage_json.
        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                """INSERT INTO captured_calls
                   (id, provider, model, method, path, usage_json, cost_json,
                    latency_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "partial-1",
                    "anthropic",
                    "claude-sonnet-4-20250514",
                    "POST",
                    "/v1/messages",
                    '{"input_tokens": 100, "output_tokens": 50}',
                    '{"input_cost": 0.0003, "output_cost": 0.00075}',
                    500.0,
                    "2025-01-01T00:00:00Z",
                ),
            )
            conn.commit()

        stats = store.get_stats_summary()
        # The row should be counted despite missing fields.
        assert stats["total_calls"] == 1
        assert stats["total_input_tokens"] == 100
        assert stats["total_output_tokens"] == 50
        assert stats["total_tokens"] == 150
        # Only the present cost fields should be summed.
        assert abs(stats["total_cost"] - 0.00105) < 1e-9

    def test_summary_handles_malformed_json(self, store: CallStore) -> None:
        """Rows with malformed JSON are treated as zero, not an error."""
        import sqlite3

        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                """INSERT INTO captured_calls
                   (id, provider, model, method, path, usage_json, cost_json,
                    latency_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "malformed-1",
                    "anthropic",
                    "claude-sonnet-4-20250514",
                    "POST",
                    "/v1/messages",
                    "not valid json",
                    "{broken",
                    500.0,
                    "2025-01-01T00:00:00Z",
                ),
            )
            conn.commit()

        # Should not raise; malformed JSON treated as zero.
        stats = store.get_stats_summary()
        assert stats["total_calls"] == 1
        assert stats["total_cost"] == 0.0
        assert stats["total_tokens"] == 0
        assert stats["avg_latency_ms"] == 500.0


class TestStatsTaskLifecycle:
    """Tests that create_proxy_app starts/stops the stats task correctly."""

    async def test_stats_task_started_on_startup(self, tmp_db: str) -> None:
        """The stats summary task is created on app startup."""
        from harness_evaluator.gateway.proxy import create_proxy_app

        store = CallStore(tmp_db)
        app, _proxy = create_proxy_app(store)

        # The on_startup and on_cleanup handlers should be registered.
        assert len(app.on_startup) >= 1
        assert len(app.on_cleanup) >= 1

    async def test_stats_task_cancelled_on_cleanup(self, tmp_db: str) -> None:
        """The stats summary task is cancelled on app cleanup without error."""
        from aiohttp.test_utils import TestServer

        from harness_evaluator.gateway.proxy import create_proxy_app

        store = CallStore(tmp_db)
        app, _proxy = create_proxy_app(store)

        server = TestServer(app)
        await server.start_server()
        # After startup, the task should be running.
        await server.close()
        # After cleanup, the task should be cancelled (no exception raised).
