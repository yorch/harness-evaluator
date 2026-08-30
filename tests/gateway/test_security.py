"""Tests for proxy security and robustness features."""

from __future__ import annotations

from datetime import UTC

import aiohttp
import pytest
from aiohttp import web

from harness_evaluator.gateway.models import Provider
from harness_evaluator.gateway.proxy import _redact_headers, create_proxy_app
from harness_evaluator.gateway.store import CallStore


class TestHeaderRedaction:
    def test_redact_authorization(self):
        headers = {"Authorization": "Bearer sk-12345", "Content-Type": "application/json"}
        redacted = _redact_headers(headers)
        assert redacted["Authorization"] == "[REDACTED]"
        assert redacted["Content-Type"] == "application/json"

    def test_redact_x_api_key(self):
        headers = {"x-api-key": "sk-ant-12345", "Accept": "application/json"}
        redacted = _redact_headers(headers)
        assert redacted["x-api-key"] == "[REDACTED]"
        assert redacted["Accept"] == "application/json"

    def test_redact_cookie(self):
        headers = {"Cookie": "session=abc123", "Set-Cookie": "token=xyz"}
        redacted = _redact_headers(headers)
        assert redacted["Cookie"] == "[REDACTED]"
        assert redacted["Set-Cookie"] == "[REDACTED]"

    def test_non_sensitive_headers_preserved(self):
        headers = {"Content-Type": "application/json", "X-Request-Id": "abc"}
        redacted = _redact_headers(headers)
        assert redacted == headers


class TestUnknownPath:
    async def test_unknown_path_returns_404(self, tmp_db):
        """Test that unknown API paths return 404 instead of defaulting to Anthropic."""
        store = CallStore(tmp_db)
        app, proxy = create_proxy_app(store, verify_ssl=False)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            async with aiohttp.ClientSession() as session, session.get(
                f"http://127.0.0.1:{port}/v1/embeddings"
            ) as resp:
                assert resp.status == 404
                body = await resp.json()
                assert "Unknown API path" in body["error"]
        finally:
            await proxy.close()
            await runner.cleanup()


class TestStoreRoundTrip:
    async def test_store_save_and_retrieve(self, tmp_db):
        """Test that a captured call survives a save→retrieve round-trip."""
        from datetime import datetime

        from harness_evaluator.gateway.models import (
            CapturedCall,
            CostBreakdown,
            TokenUsage,
        )

        store = CallStore(tmp_db)
        original = CapturedCall(
            id="test-123",
            trace_id="trace-456",
            parent_id=None,
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            method="POST",
            path="/v1/messages",
            request_headers={"x-api-key": "[REDACTED]", "Content-Type": "application/json"},
            request_body={"model": "claude-sonnet-4-20250514", "messages": []},
            response_status=200,
            response_headers={"Content-Type": "application/json"},
            response_body={"id": "msg_1", "usage": {"input_tokens": 100}},
            usage=TokenUsage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=10,
                cache_write_tokens=5,
            ),
            cost=CostBreakdown(
                input_cost=0.0003,
                output_cost=0.00075,
                cache_read_cost=0.000003,
                cache_write_cost=0.00001875,
            ),
            latency_ms=123.45,
            timestamp=datetime.now(UTC),
            is_streaming=False,
            error=None,
        )

        store.save(original)
        retrieved = store.get_by_id("test-123")

        assert retrieved is not None
        assert retrieved.id == "test-123"
        assert retrieved.trace_id == "trace-456"
        assert retrieved.provider == Provider.ANTHROPIC
        assert retrieved.model == "claude-sonnet-4-20250514"
        assert retrieved.method == "POST"
        assert retrieved.path == "/v1/messages"
        assert retrieved.request_headers["x-api-key"] == "[REDACTED]"
        assert retrieved.request_body == {"model": "claude-sonnet-4-20250514", "messages": []}
        assert retrieved.response_status == 200
        assert retrieved.usage.input_tokens == 100
        assert retrieved.usage.output_tokens == 50
        assert retrieved.usage.cache_read_tokens == 10
        assert retrieved.usage.cache_write_tokens == 5
        assert retrieved.cost.input_cost == pytest.approx(0.0003)
        assert retrieved.latency_ms == pytest.approx(123.45)
        assert retrieved.is_streaming is False
        assert retrieved.error is None

    async def test_store_get_by_trace(self, tmp_db):
        """Test retrieving calls by trace ID."""
        from harness_evaluator.gateway.models import CapturedCall

        store = CallStore(tmp_db)
        for i in range(3):
            call = CapturedCall(
                id=f"call-{i}",
                trace_id="trace-shared",
                provider=Provider.ANTHROPIC,
                model="claude-sonnet-4-20250514",
                method="POST",
                path="/v1/messages",
            )
            store.save(call)

        # Add one with different trace
        other = CapturedCall(
            id="call-other",
            trace_id="trace-other",
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            method="POST",
            path="/v1/messages",
        )
        store.save(other)

        results = store.get_by_trace("trace-shared")
        assert len(results) == 3
        assert all(r.trace_id == "trace-shared" for r in results)


class TestAuthHeadersRedactedInStorage:
    async def test_auth_not_stored(self, proxy_app):
        """Test that API keys are not stored in the database."""
        proxy_url, store, _ = proxy_app

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": "sk-ant-secret123", "Authorization": "Bearer token456"},
        ) as resp:
            assert resp.status == 200

        calls = store.get_all()
        assert len(calls) == 1
        call = calls[0]
        # Check that sensitive headers are redacted
        for key, value in call.request_headers.items():
            if key.lower() in ("authorization", "x-api-key"):
                assert value == "[REDACTED]", f"Header {key} was not redacted: {value}"
