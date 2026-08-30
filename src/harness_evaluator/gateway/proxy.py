"""Custom HTTP/SSE proxy server for provider API call capture.

The proxy sits between the harness and the provider API. It:
  1. Accepts HTTP requests from the harness (which is configured to use
     ANTHROPIC_BASE_URL=http://localhost:PORT or OPENAI_BASE_URL=...).
  2. Forwards them to the real provider API over HTTPS (with TLS verification).
  3. Parses streaming (SSE) and non-streaming responses to extract token usage.
  4. Records everything (tokens, cost, latency, request/response bodies) to the
     CallStore, with sensitive headers redacted.
  5. Returns the response transparently to the harness.

No TLS interception is needed — the harness talks HTTP to localhost, and the
proxy talks HTTPS to the real provider with full certificate verification.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl as ssl_module
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
from aiohttp import web

from harness_evaluator.gateway.models import (
    CapturedCall,
    Provider,
    TokenUsage,
    get_pricing_strict,
)
from harness_evaluator.gateway.parsers import anthropic as anthropic_parser
from harness_evaluator.gateway.parsers import openai as openai_parser
from harness_evaluator.gateway.store import CallStore

logger = logging.getLogger(__name__)

# Headers we should not forward back to the client (hop-by-hop headers)
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# Internal harness-evaluator trace headers — extracted for attribution but never
# forwarded to the real upstream provider API.
INTERNAL_TRACE_HEADERS = frozenset(
    {
        "x-harness-evaluator-trace-id",
        "x-trace-id",
    }
)

# Headers that contain sensitive data and must be redacted before storage.
# Expanded to cover common auth/session/token headers across providers
# and intermediaries.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "x-goog-api-key",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-amz-security-token",
        "x-auth-token",
        "x-session-token",
        "x-access-token",
        "api-key",
        "openai-organization",
        "anthropic-organization",
    }
)

# Substrings that indicate a secret in a header name (case-insensitive).
# Catches headers like "x-anthropic-api-key", "openai-api-key", etc.
_SENSITIVE_HEADER_SUBSTRINGS = ("key", "token", "secret", "auth", "cookie", "password")

# Real provider upstream URLs
UPSTREAM_URLS: dict[Provider, str] = {
    Provider.ANTHROPIC: "https://api.anthropic.com",
    Provider.OPENAI: "https://api.openai.com",
}

# Spoofable routing headers that must never be forwarded upstream (a harness
# could otherwise set x-forwarded-* / via to influence the provider or an
# intermediary). Auth and SDK headers are still forwarded normally.
_FORWARD_DENY_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-real-ip",
        "forwarded",
        "via",
    }
)


def _validate_upstream_url(url: str) -> str:
    """Validate an upstream override URL to prevent SSRF/proxy abuse.

    ``upstream_overrides`` is a programmatic/testing hook (not exposed on the
    CLI), so http + loopback are permitted for local mock servers. What is
    always rejected is a non-http(s) scheme and the cloud metadata / link-local
    endpoints that are the classic SSRF targets.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Upstream URL must use http(s): {url!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"Upstream URL has no host: {url!r}")
    if host == "metadata.google.internal" or host.startswith("169.254."):
        raise ValueError(f"Upstream URL host is not allowed (metadata/link-local): {host!r}")
    return url

# Maximum request/response body size to store (10 MB)
MAX_STORED_BODY_SIZE = 10 * 1024 * 1024

# Maximum SSE raw text to store (100 KB)
MAX_SSE_STORED_SIZE = 100_000

# Request timeout in seconds
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=300)


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact sensitive headers before storing to disk.

    Matches both an explicit allow-list (SENSITIVE_HEADERS) and a
    substring heuristic for common secret-bearing header names.
    """
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in SENSITIVE_HEADERS or any(s in lower for s in _SENSITIVE_HEADER_SUBSTRINGS):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


class GatewayProxy:
    """HTTP/SSE proxy that captures provider API calls."""

    def __init__(
        self,
        store: CallStore,
        upstream_overrides: dict[Provider, str] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self.store = store
        validated_overrides = {
            provider: _validate_upstream_url(url)
            for provider, url in (upstream_overrides or {}).items()
        }
        self.upstreams = {**UPSTREAM_URLS, **validated_overrides}
        self.verify_ssl = verify_ssl
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        # Guard against concurrent session creation. Without the lock,
        # two coroutines could race and create overlapping sessions.
        async with self._session_lock:
            if self._session is None or self._session.closed:
                ssl_ctx: ssl_module.SSLContext | bool
                ssl_ctx = (
                    ssl_module.create_default_context()
                    if self.verify_ssl
                    else False
                )
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                self._session = aiohttp.ClientSession(
                    timeout=REQUEST_TIMEOUT,
                    connector=connector,
                )
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _detect_provider(self, path: str) -> Provider | None:
        """Detect which provider based on the API path.

        Returns None for unknown paths.
        """
        if path.startswith("/v1/messages"):
            return Provider.ANTHROPIC
        if path.startswith("/v1/chat/completions") or path.startswith("/v1/responses"):
            return Provider.OPENAI
        return None

    def _extract_model(self, provider: Provider, body: dict[str, Any]) -> str:
        """Extract model name from request body."""
        model = body.get("model", "unknown")
        return str(model)

    def _extract_trace_id(
        self, headers: Mapping[str, str], query_string: str = ""
    ) -> str | None:
        """Extract trace ID from request headers or query string.

        The harness subprocesses append ``?trace_id=xxx`` to the gateway
        URL (via the adapter's ``get_env()``), so we check the query
        string in addition to the explicit ``x-harness-evaluator-trace-id`` header.
        """
        trace_id = headers.get("x-harness-evaluator-trace-id") or headers.get("x-trace-id")
        if trace_id:
            return trace_id
        if query_string:
            params = parse_qs(query_string)
            trace_ids = params.get("trace_id")
            if trace_ids:
                return trace_ids[0]
        return None

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Handle all incoming requests — forward to upstream and capture."""
        provider = self._detect_provider(request.path)
        if provider is None:
            logger.warning("Unknown API path: %s, returning 404", request.path)
            return web.Response(
                status=404,
                text=json.dumps({"error": f"Unknown API path: {request.path}"}),
                content_type="application/json",
            )

        upstream = self.upstreams[provider]

        # Read request body (with size limit)
        body_bytes = await request.read()
        if len(body_bytes) > MAX_STORED_BODY_SIZE:
            logger.warning("Request body too large: %d bytes", len(body_bytes))
            return web.Response(status=413, text="Request body too large")

        request_body: dict[str, Any] | None = None
        if body_bytes:
            try:
                parsed = json.loads(body_bytes)
                # Only treat JSON objects as the request body; arrays/scalars
                # would break the later ``.get(...)`` calls with AttributeError.
                request_body = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                request_body = None

        model = self._extract_model(provider, request_body or {})
        is_streaming = bool(request_body and request_body.get("stream", False))
        trace_id = self._extract_trace_id(
            request.headers, request.query_string
        )
        call_id = str(uuid.uuid4())

        # Build upstream URL, stripping the trace_id query param so it
        # is not forwarded to the real provider API.
        upstream_url = f"{upstream}{request.path}"
        if request.query_string:
            upstream_params = parse_qs(request.query_string)
            upstream_params.pop("trace_id", None)
            if upstream_params:
                # Re-encode without trace_id
                filtered = urlencode(
                    {k: v[0] for k, v in upstream_params.items()}
                )
                upstream_url += f"?{filtered}"

        # Forward headers (strip hop-by-hop and internal trace headers,
        # keep auth and content-type)
        forward_headers: dict[str, str] = {}
        for key, value in request.headers.items():
            lower = key.lower()
            if (
                lower not in HOP_BY_HOP
                and lower not in INTERNAL_TRACE_HEADERS
                and lower not in _FORWARD_DENY_HEADERS
            ):
                forward_headers[key] = value
        forward_headers["Host"] = urlparse(upstream).netloc

        logger.info(
            "Proxying %s %s -> %s (model=%s, streaming=%s, call_id=%s)",
            request.method,
            request.path,
            upstream_url,
            model,
            is_streaming,
            call_id,
        )

        start_time = time.monotonic()
        session = await self.get_session()

        # Redact headers for storage
        redacted_request_headers = _redact_headers(dict(request.headers))

        try:
            async with session.request(
                request.method,
                upstream_url,
                headers=forward_headers,
                data=body_bytes if body_bytes else None,
            ) as upstream_resp:
                if is_streaming:
                    return await self._handle_streaming(
                        request,
                        upstream_resp,
                        provider,
                        model,
                        call_id,
                        trace_id,
                        request_body,
                        redacted_request_headers,
                        start_time,
                    )
                else:
                    return await self._handle_non_streaming(
                        request,
                        upstream_resp,
                        provider,
                        model,
                        call_id,
                        trace_id,
                        request_body,
                        redacted_request_headers,
                        start_time,
                    )
        except aiohttp.ClientError as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.error("Upstream error for call %s: %s", call_id, e)
            call = CapturedCall(
                id=call_id,
                trace_id=trace_id,
                provider=provider,
                model=model,
                method=request.method,
                path=request.path,
                request_headers=redacted_request_headers,
                request_body=request_body,
                response_status=0,
                latency_ms=latency_ms,
                is_streaming=is_streaming,
                error=str(e),
            )
            await asyncio.to_thread(self.store.save, call)
            return web.Response(status=502, text=f"Gateway error: {e}")
        except TimeoutError:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.error("Upstream timeout for call %s after %ss", call_id, REQUEST_TIMEOUT)
            call = CapturedCall(
                id=call_id,
                trace_id=trace_id,
                provider=provider,
                model=model,
                method=request.method,
                path=request.path,
                request_headers=redacted_request_headers,
                request_body=request_body,
                response_status=0,
                latency_ms=latency_ms,
                is_streaming=is_streaming,
                error=f"Upstream timeout after {REQUEST_TIMEOUT.total}s",
            )
            await asyncio.to_thread(self.store.save, call)
            return web.Response(status=504, text="Gateway timeout")

    async def _handle_non_streaming(
        self,
        request: web.Request,
        upstream_resp: aiohttp.ClientResponse,
        provider: Provider,
        model: str,
        call_id: str,
        trace_id: str | None,
        request_body: dict[str, Any] | None,
        redacted_request_headers: dict[str, str],
        start_time: float,
    ) -> web.Response:
        """Handle non-streaming response: read body, parse usage, return."""
        body_bytes = await upstream_resp.read()
        latency_ms = (time.monotonic() - start_time) * 1000

        response_body: dict[str, Any] | None = None
        try:
            response_body = json.loads(body_bytes)
        except json.JSONDecodeError:
            response_body = None

        # Parse usage
        usage = self._parse_non_streaming_usage(provider, response_body or {})
        pricing = get_pricing_strict(model)
        cost = pricing.calculate(usage)

        # Redact response headers for storage
        redacted_response_headers = _redact_headers(dict(upstream_resp.headers))

        # Save captured call
        call = CapturedCall(
            id=call_id,
            trace_id=trace_id,
            provider=provider,
            model=model,
            method=request.method,
            path=request.path,
            request_headers=redacted_request_headers,
            request_body=request_body,
            response_status=upstream_resp.status,
            response_headers=redacted_response_headers,
            response_body=response_body,
            usage=usage,
            cost=cost,
            latency_ms=latency_ms,
            is_streaming=False,
        )
        await asyncio.to_thread(self.store.save, call)

        logger.info(
            "Captured %s: %s tokens, $%.6f, %.0fms",
            call_id,
            usage.total_tokens,
            cost.total,
            latency_ms,
        )

        # Build response, filtering hop-by-hop headers
        resp = web.Response(
            status=upstream_resp.status,
            body=body_bytes,
        )
        for key, value in upstream_resp.headers.items():
            if key.lower() not in HOP_BY_HOP:
                resp.headers[key] = value
        return resp

    async def _handle_streaming(
        self,
        request: web.Request,
        upstream_resp: aiohttp.ClientResponse,
        provider: Provider,
        model: str,
        call_id: str,
        trace_id: str | None,
        request_body: dict[str, Any] | None,
        redacted_request_headers: dict[str, str],
        start_time: float,
    ) -> web.StreamResponse:
        """Handle streaming (SSE) response: parse stream in real-time, capture usage.

        Buffers raw bytes and splits on newline boundaries to avoid corrupting
        SSE events at byte-chunk boundaries.
        """
        # Create streaming response
        resp = web.StreamResponse(
            status=upstream_resp.status,
            headers={
                k: v
                for k, v in upstream_resp.headers.items()
                if k.lower() not in HOP_BY_HOP
            },
        )
        resp.content_type = "text/event-stream"
        await resp.prepare(request)

        accumulated_usage = TokenUsage()
        # Buffer raw bytes for SSE parsing (split on \n boundary)
        byte_buffer = b""
        # Track total SSE text for storage (capped)
        sse_text_parts: list[str] = []
        sse_text_size = 0
        stream_error: str | None = None
        # Per-stream SSE event state (NOT shared on the proxy instance,
        # so concurrent streams do not overwrite each other).
        current_event: str | None = None

        try:
            async for chunk in upstream_resp.content.iter_any():
                # Forward chunk to client immediately
                await resp.write(chunk)

                # Accumulate for storage (capped)
                if sse_text_size < MAX_SSE_STORED_SIZE:
                    chunk_text = chunk.decode("utf-8", errors="replace")
                    sse_text_parts.append(chunk_text)
                    sse_text_size += len(chunk_text)

                # Buffer raw bytes and process complete lines
                byte_buffer += chunk
                while b"\n" in byte_buffer:
                    line_bytes, byte_buffer = byte_buffer.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if line:
                        current_event, accumulated_usage = self._process_sse_line(
                            provider, line, accumulated_usage, current_event
                        )

            # Process any remaining buffered data
            if byte_buffer:
                line = byte_buffer.decode("utf-8", errors="replace").strip()
                if line:
                    current_event, accumulated_usage = self._process_sse_line(
                        provider, line, accumulated_usage, current_event
                    )

        except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError) as e:
            stream_error = f"Stream interrupted: {e}"
            logger.error("Stream error for call %s: %s", call_id, e)
        except Exception as e:
            stream_error = f"Unexpected stream error: {e}"
            logger.error("Unexpected stream error for call %s: %s", call_id, e)

        # NOTE: do not write_eof() yet. We persist the captured call *before*
        # signaling end-of-stream to the client, otherwise the client's read
        # loop can finish (and inspect the store) before the background save
        # commits — a race that made streaming-capture tests flaky.
        latency_ms = (time.monotonic() - start_time) * 1000

        # Calculate cost
        pricing = get_pricing_strict(model)
        cost = pricing.calculate(accumulated_usage)

        # Build SSE summary for storage
        raw_sse = "".join(sse_text_parts)
        if len(raw_sse) >= MAX_SSE_STORED_SIZE:
            raw_sse = raw_sse[:MAX_SSE_STORED_SIZE] + "...[truncated]"
        response_body: dict[str, Any] | None = {
            "_sse_raw": raw_sse,
            "_note": "Reconstructed from SSE stream",
        }

        # Redact response headers for storage
        redacted_response_headers = _redact_headers(dict(upstream_resp.headers))

        # Save captured call
        call = CapturedCall(
            id=call_id,
            trace_id=trace_id,
            provider=provider,
            model=model,
            method=request.method,
            path=request.path,
            request_headers=redacted_request_headers,
            request_body=request_body,
            response_status=upstream_resp.status,
            response_headers=redacted_response_headers,
            response_body=response_body,
            usage=accumulated_usage,
            cost=cost,
            latency_ms=latency_ms,
            is_streaming=True,
            error=stream_error,
        )
        await asyncio.to_thread(self.store.save, call)

        # Now that the call is persisted, signal end-of-stream to the client.
        with contextlib.suppress(Exception):
            await resp.write_eof()  # Client may have disconnected

        logger.info(
            "Captured streaming %s: %s tokens, $%.6f, %.0fms%s",
            call_id,
            accumulated_usage.total_tokens,
            cost.total,
            latency_ms,
            f" [error: {stream_error}]" if stream_error else "",
        )

        return resp

    def _process_sse_line(
        self,
        provider: Provider,
        line: str,
        accumulated: TokenUsage,
        current_event: str | None,
    ) -> tuple[str | None, TokenUsage]:
        """Process a single complete SSE line and update accumulated usage.

        This processes one line at a time (no splitting) to avoid the
        byte-boundary corruption issue. The current Anthropic event type
        is passed in and returned so per-stream state stays local to the
        caller (concurrent streams do not share event state).
        """
        if provider == Provider.ANTHROPIC:
            event_type, data = anthropic_parser.parse_sse_line(line)
            if event_type:
                # Remember event type for the next data line
                current_event = event_type
            if data and current_event:
                accumulated = anthropic_parser.parse_sse_event(
                    current_event, data, accumulated
                )
        elif provider == Provider.OPENAI:
            _, data = openai_parser.parse_sse_line(line)
            if data:
                accumulated = openai_parser.parse_sse_chunk(data, accumulated)

        return current_event, accumulated

    def _parse_non_streaming_usage(
        self, provider: Provider, body: dict[str, Any]
    ) -> TokenUsage:
        """Parse usage from a non-streaming response body."""
        if provider == Provider.ANTHROPIC:
            return anthropic_parser.parse_non_streaming_usage(body)
        elif provider == Provider.OPENAI:
            return openai_parser.parse_non_streaming_usage(body)
        return TokenUsage()


def create_proxy_app(
    store: CallStore,
    upstream_overrides: dict[Provider, str] | None = None,
    verify_ssl: bool = True,
) -> tuple[web.Application, GatewayProxy]:
    """Create the aiohttp application and proxy instance."""
    proxy = GatewayProxy(store, upstream_overrides, verify_ssl=verify_ssl)

    app = web.Application()
    # Catch-all route: forward everything
    app.router.add_route("*", "/{tail:.*}", proxy.handle)

    # Store proxy on app for cleanup using AppKey
    proxy_key = web.AppKey("proxy", GatewayProxy)
    app[proxy_key] = proxy

    async def cleanup(app: web.Application) -> None:
        await app[proxy_key].close()

    app.on_cleanup.append(cleanup)

    return app, proxy


def run_proxy(
    host: str = "127.0.0.1",
    port: int = 8877,
    db_path: str = "harness_evaluator_gateway.db",
    upstream_overrides: dict[Provider, str] | None = None,
    verify_ssl: bool = True,
) -> None:
    """Run the gateway proxy server."""
    store = CallStore(db_path)
    app, proxy = create_proxy_app(
        store, upstream_overrides, verify_ssl=verify_ssl
    )
    web.run_app(app, host=host, port=port, print=lambda msg: logger.info(msg))
