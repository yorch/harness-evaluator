"""Test fixtures for gateway tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aiohttp import web

from heval.gateway.models import Provider
from heval.gateway.proxy import create_proxy_app
from heval.gateway.store import CallStore


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary SQLite DB path."""
    return str(tmp_path / "test_gateway.db")


@pytest.fixture
async def proxy_app(tmp_db, mock_anthropic_server):
    """Create a proxy app that forwards to the mock Anthropic server."""
    store = CallStore(tmp_db)
    upstream_url, _ = mock_anthropic_server
    app, proxy = create_proxy_app(
        store,
        upstream_overrides={Provider.ANTHROPIC: upstream_url},
        verify_ssl=False,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}", store, proxy

    await proxy.close()
    await runner.cleanup()


@pytest.fixture
async def proxy_app_openai(tmp_db, mock_openai_server):
    """Create a proxy app that forwards to the mock OpenAI server."""
    store = CallStore(tmp_db)
    upstream_url, _ = mock_openai_server
    app, proxy = create_proxy_app(
        store,
        upstream_overrides={Provider.OPENAI: upstream_url},
        verify_ssl=False,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}", store, proxy

    await proxy.close()
    await runner.cleanup()


@pytest.fixture
async def mock_anthropic_server(tmp_path):
    """Start a mock Anthropic API server for testing.

    Returns (base_url, call_records) where call_records is a list of
    requests received by the mock server.
    """
    call_records: list[dict[str, Any]] = []

    async def handle_messages(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        is_streaming = body.get("stream", False)
        call_records.append({"path": request.path, "body": body})

        if is_streaming:
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await resp.prepare(request)

            # Simulate Anthropic SSE stream
            input_tokens = 100
            output_tokens = 50

            events = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "model": body.get("model", "claude-sonnet-4-20250514"),
                            "content": [],
                            "stop_reason": None,
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": 0,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                            },
                        },
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hello"},
                    },
                ),
                (
                    "content_block_stop",
                    {"type": "content_block_stop", "index": 0},
                ),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": output_tokens},
                    },
                ),
                (
                    "message_stop",
                    {"type": "message_stop"},
                ),
            ]

            for event_type, data in events:
                await resp.write(f"event: {event_type}\n".encode())
                await resp.write(f"data: {json.dumps(data)}\n\n".encode())

            await resp.write_eof()
            return resp
        else:
            # Non-streaming response
            usage = {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
            return web.json_response(
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model", "claude-sonnet-4-20250514"),
                    "content": [{"type": "text", "text": "Hello!"}],
                    "stop_reason": "end_turn",
                    "usage": usage,
                }
            )

    app = web.Application()
    app.router.add_post("/v1/messages", handle_messages)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    base_url = f"http://127.0.0.1:{port}"

    yield base_url, call_records

    await runner.cleanup()


@pytest.fixture
async def mock_openai_server(tmp_path):
    """Start a mock OpenAI API server for testing."""
    call_records: list[dict[str, Any]] = []

    async def handle_chat(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        is_streaming = body.get("stream", False)
        call_records.append({"path": request.path, "body": body})

        if is_streaming:
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await resp.prepare(request)

            # Simulate OpenAI SSE stream with usage in final chunk
            chunks = [
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": "Hi"}}],
                },
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": "!"}}],
                },
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70,
                }},
            ]

            for chunk in chunks:
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        else:
            return web.json_response({
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": body.get("model", "gpt-4o"),
                "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70,
                },
            })

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handle_chat)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    base_url = f"http://127.0.0.1:{port}"

    yield base_url, call_records

    await runner.cleanup()
