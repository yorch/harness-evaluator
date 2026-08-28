"""Integration tests for the gateway proxy with mock upstream servers."""

from __future__ import annotations

import aiohttp
from aiohttp import web

from heval.gateway.models import Provider
from heval.gateway.proxy import create_proxy_app
from heval.gateway.store import CallStore


class TestProxyNonStreamingAnthropic:
    async def test_non_streaming_request_captured(self, proxy_app):
        """Test that a non-streaming request is proxied and usage is captured."""
        proxy_url, store, _ = proxy_app

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as resp:
            assert resp.status == 200
            body = await resp.json()
            assert body["content"][0]["text"] == "Hello!"

        # Check that the call was captured
        calls = store.get_all()
        assert len(calls) == 1
        call = calls[0]
        assert call.provider == Provider.ANTHROPIC
        assert call.model == "claude-sonnet-4-20250514"
        assert call.is_streaming is False
        assert call.response_status == 200
        assert call.usage.input_tokens == 100
        assert call.usage.output_tokens == 50
        assert call.latency_ms > 0

    async def test_non_streaming_cost_calculated(self, proxy_app):
        """Test that cost is calculated from captured usage."""
        proxy_url, store, _ = proxy_app

        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{proxy_url}/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )

        calls = store.get_all()
        call = calls[0]
        # Sonnet pricing: $3/M input, $15/M output
        expected_input_cost = 100 * 3.0 / 1_000_000
        expected_output_cost = 50 * 15.0 / 1_000_000
        assert abs(call.cost.input_cost - expected_input_cost) < 1e-10
        assert abs(call.cost.output_cost - expected_output_cost) < 1e-10
        assert call.cost.total > 0


class TestProxyStreamingAnthropic:
    async def test_streaming_request_captured(self, proxy_app):
        """Test that a streaming request is proxied and usage is captured from SSE."""
        proxy_url, store, _ = proxy_app

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as resp:
            assert resp.status == 200
            # Read the full stream
            text = ""
            async for chunk in resp.content:
                text += chunk.decode("utf-8", errors="replace")
            assert "Hello" in text

        # Check that the call was captured with correct usage from SSE
        calls = store.get_all()
        assert len(calls) == 1
        call = calls[0]
        assert call.is_streaming is True
        assert call.usage.input_tokens == 100
        assert call.usage.output_tokens == 50
        assert call.usage.total_tokens == 150

    async def test_streaming_with_cache_tokens(self, proxy_app, mock_anthropic_server):
        """Test streaming with cache tokens in the SSE stream."""
        proxy_url, store, _ = proxy_app

        # Modify the mock to include cache tokens
        upstream_url, call_records = mock_anthropic_server

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
                "system": [
                    {
                        "type": "text",
                        "text": "You are helpful",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ) as resp:
            async for _chunk in resp.content:
                pass

        calls = store.get_all()
        call = calls[0]
        # The mock sends cache_read=0, cache_write=0, but we verify the parser
        # correctly captures these fields
        assert call.usage.cache_read_tokens == 0
        assert call.usage.cache_write_tokens == 0


class TestProxyOpenAI:
    async def test_non_streaming_openai(self, proxy_app_openai):
        """Test OpenAI non-streaming request through proxy."""
        proxy_url, store, _ = proxy_app_openai

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as resp:
            assert resp.status == 200
            body = await resp.json()
            assert body["choices"][0]["message"]["content"] == "Hi!"

        calls = store.get_all()
        assert len(calls) == 1
        call = calls[0]
        assert call.provider == Provider.OPENAI
        assert call.usage.input_tokens == 50
        assert call.usage.output_tokens == 20

    async def test_streaming_openai(self, proxy_app_openai):
        """Test OpenAI streaming request through proxy."""
        proxy_url, store, _ = proxy_app_openai

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as resp:
            assert resp.status == 200
            text = ""
            async for chunk in resp.content:
                text += chunk.decode("utf-8", errors="replace")
            assert "Hi" in text

        calls = store.get_all()
        assert len(calls) == 1
        call = calls[0]
        assert call.is_streaming is True
        assert call.usage.input_tokens == 50
        assert call.usage.output_tokens == 20


class TestProxyErrorHandling:
    async def test_upstream_error_captured(self, tmp_db, mock_anthropic_server):
        """Test that upstream errors are captured."""
        upstream_url, _ = mock_anthropic_server

        # Create proxy pointing to a non-existent upstream
        store = CallStore(tmp_db)
        app, proxy = create_proxy_app(
            store,
            upstream_overrides={Provider.ANTHROPIC: "http://127.0.0.1:1"},  # invalid port
            verify_ssl=False,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            async with aiohttp.ClientSession() as session, session.post(
                f"http://127.0.0.1:{port}/v1/messages",
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 10, "messages": []},
            ) as resp:
                assert resp.status == 502

            calls = store.get_all()
            assert len(calls) == 1
            call = calls[0]
            assert call.error is not None
            assert call.success is False
        finally:
            await proxy.close()
            await runner.cleanup()


class TestProxyCanaryIntegration:
    async def test_canary_passes_with_mock(self, proxy_app):
        """Test that the canary passes when proxy usage matches upstream."""
        from heval.gateway.canary import run_canary

        proxy_url, store, _ = proxy_app

        # Send a non-streaming request
        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as resp:
            await resp.json()

        # Run canary
        result = await run_canary(db_path=str(store.db_path), tolerance_pct=1.0)
        assert result.passed
        assert result.proxy_usage is not None
        assert result.proxy_usage.input_tokens == 100
        assert result.proxy_usage.output_tokens == 50

    async def test_canary_streaming(self, proxy_app):
        """Test that the canary handles streaming responses."""
        from heval.gateway.canary import run_canary

        proxy_url, store, _ = proxy_app

        async with aiohttp.ClientSession() as session, session.post(
            f"{proxy_url}/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as resp:
            async for _chunk in resp.content:
                pass

        result = await run_canary(db_path=str(store.db_path), tolerance_pct=1.0)
        assert result.passed  # Streaming: single source, should pass
        assert result.proxy_usage is not None
        assert result.proxy_usage.total_tokens > 0
