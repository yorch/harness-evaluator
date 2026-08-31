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
import errno as errno_module
import json
import logging
import socket
import ssl as ssl_module
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
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
        # aiohttp auto-decompresses the upstream response body when reading
        # it (via .read() or .content.iter_any()). If we forward the original
        # Content-Encoding header, the client sees e.g. "br" and tries to
        # decompress already-decompressed bytes, causing
        # BrotliDecompressionError / encoding errors. Strip it so the client
        # receives plain uncompressed bytes.
        "content-encoding",
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
    Provider.OPENAI_CHATGPT: "https://chatgpt.com/backend-api",
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

# Interval (seconds) between periodic aggregated stats summaries.
STATS_SUMMARY_INTERVAL = 30.0


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
        if path.startswith("/codex/responses"):
            return Provider.OPENAI_CHATGPT
        return None

    def _extract_model(self, provider: Provider, body: dict[str, Any]) -> str:
        """Extract model name from request body."""
        model = body.get("model", "unknown")
        return str(model)

    def _extract_trace_id(
        self,
        headers: Mapping[str, str],
        query_string: str = "",
        path: str = "",
    ) -> tuple[str | None, str]:
        """Extract trace ID from request headers, path prefix, or query string.

        The primary mechanism is the ``/__trace__/<id>`` path prefix
        (inserted by the adapter's ``_gateway_url_with_trace``), which
        survives HTTP client URL joining. The query string ``?trace_id=``
        is kept as a backward-compatible fallback. The
        ``x-harness-evaluator-trace-id`` / ``x-trace-id`` headers are
        also checked.

        Returns ``(trace_id, stripped_path)`` where ``stripped_path`` is
        the request path with the ``/__trace__/<id>`` prefix removed (or
        the original path if no prefix was present).
        """
        # 1. Check explicit headers first.
        trace_id = headers.get("x-harness-evaluator-trace-id") or headers.get(
            "x-trace-id"
        )
        stripped_path = path
        if trace_id:
            return trace_id, stripped_path

        # 2. Check path prefix /__trace__/<id>/...
        if path and path.startswith("/__trace__/"):
            rest = path[len("/__trace__/"):]
            slash = rest.find("/")
            if slash > 0:
                trace_id = rest[:slash]
                stripped_path = rest[slash:]
            elif slash == -1:
                # /__trace__/<id> with no trailing path — unlikely but handle it
                trace_id = rest
                stripped_path = "/"
            if trace_id:
                return trace_id, stripped_path

        # 3. Fallback: query string ?trace_id=xxx
        if query_string:
            params = parse_qs(query_string)
            trace_ids = params.get("trace_id")
            if trace_ids:
                return trace_ids[0], stripped_path
        return None, stripped_path

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Handle all incoming requests — forward to upstream and capture."""
        # Extract trace ID from path prefix and get the stripped path for
        # provider detection and upstream forwarding.
        trace_id, stripped_path = self._extract_trace_id(
            request.headers, request.query_string, request.path
        )

        provider = self._detect_provider(stripped_path)
        if provider is None:
            logger.warning("Unknown API path: %s, returning 404", stripped_path)
            return web.Response(
                status=404,
                text=json.dumps({"error": f"Unknown API path: {stripped_path}"}),
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
        call_id = str(uuid.uuid4())

        # Build upstream URL using the stripped path (no __trace__ prefix)
        # and without the trace_id query param so neither is forwarded.
        upstream_url = f"{upstream}{stripped_path}"
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
                        stripped_path,
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
                        stripped_path,
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
                path=stripped_path,
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
                path=stripped_path,
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
        stripped_path: str,
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
            path=stripped_path,
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
        stripped_path: str,
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
            path=stripped_path,
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
        elif provider in (Provider.OPENAI, Provider.OPENAI_CHATGPT):
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
        elif provider in (Provider.OPENAI, Provider.OPENAI_CHATGPT):
            return openai_parser.parse_non_streaming_usage(body)
        return TokenUsage()


async def _stats_summary_loop(
    store: CallStore, start_time: float
) -> None:
    """Periodically log an aggregated stats summary.

    Runs every ``STATS_SUMMARY_INTERVAL`` seconds while the gateway is up.
    Only emits when logging is at INFO level or below (i.e. ``-v`` is
    passed); otherwise it is a no-op that returns immediately so the
    gateway does not waste cycles querying the DB when quiet.
    """
    if not logger.isEnabledFor(logging.INFO):
        return

    last_summary = datetime.now(UTC)
    while True:
        await asyncio.sleep(STATS_SUMMARY_INTERVAL)
        if not logger.isEnabledFor(logging.INFO):
            continue
        now = datetime.now(UTC)
        try:
            stats = await asyncio.to_thread(store.get_stats_summary)
            calls_in_interval = await asyncio.to_thread(
                store.get_calls_since, last_summary.isoformat()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Stats summary query failed: %s", e, exc_info=True)
            continue
        last_summary = now

        uptime = time.monotonic() - start_time
        logger.info(
            "Gateway stats summary:\n"
            "  Uptime: %.0fs\n"
            "  Total calls captured: %d\n"
            "  Calls in last interval: %d\n"
            "  Total cost: $%.4f\n"
            "  Total tokens: %d (input: %d, output: %d)\n"
            "  Average latency: %.0fms",
            uptime,
            stats["total_calls"],
            calls_in_interval,
            stats["total_cost"],
            stats["total_tokens"],
            stats["total_input_tokens"],
            stats["total_output_tokens"],
            stats["avg_latency_ms"],
        )


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

    start_time = time.monotonic()

    # Periodic aggregated stats summary (only emits at INFO level or below).
    stats_task: asyncio.Task[None] | None = None

    async def start_stats_task(app: web.Application) -> None:
        nonlocal stats_task
        stats_task = asyncio.create_task(
            _stats_summary_loop(store, start_time)
        )

    async def stop_stats_task(app: web.Application) -> None:
        nonlocal stats_task
        if stats_task is not None and not stats_task.done():
            stats_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stats_task
            stats_task = None

    app.on_startup.append(start_stats_task)
    app.on_cleanup.append(stop_stats_task)

    async def cleanup(app: web.Application) -> None:
        await app[proxy_key].close()

    app.on_cleanup.append(cleanup)

    return app, proxy


class GatewayStartupError(Exception):
    """Raised when the gateway proxy cannot start (e.g. port in use)."""

    def __init__(
        self,
        message: str,
        *,
        errno: int | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.errno = errno
        self.host = host
        self.port = port


def _format_addrinuse_error(host: str, port: int) -> str:
    """Build a user-friendly error message for EADDRINUSE."""
    return (
        f"Port {port} is already in use on {host}.\n"
        f"This usually means another gateway (or another process) is "
        f"already listening on that port.\n"
        f"Options:\n"
        f"  - Stop the other process and retry\n"
        f"  - Use a different port: harness-evaluator gateway --port {port + 1}\n"
        f"  - Check what is listening: lsof -i :{port} (Linux/macOS) "
        f"or netstat -ano | findstr :{port} (Windows)"
    )


def _format_eacces_error(host: str, port: int) -> str:
    """Build a user-friendly error message for EACCES (privileged port)."""
    return (
        f"Permission denied binding to {host}:{port}.\n"
        f"Ports below 1024 require elevated privileges.\n"
        f"Use a port >= 1024: harness-evaluator gateway --port 8877"
    )


def _check_port_available(host: str, port: int) -> None:
    """Preflight check that the port is bindable.

    Raises ``GatewayStartupError`` with a user-friendly message if the port
    is invalid, already in use, requires elevated privileges, or the host
    cannot be resolved. Supports both IPv4 and IPv6 hosts.
    """
    if not (1 <= port <= 65535):
        raise GatewayStartupError(
            f"Invalid port number: {port}. Must be between 1 and 65535.",
            host=host,
            port=port,
        )

    # Resolve the host to support IPv4, IPv6, and hostnames.
    try:
        infos = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise GatewayStartupError(
            f"Cannot resolve host {host!r}: {exc}",
            host=host,
            port=port,
        ) from exc

    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(sockaddr)
        except OSError as exc:
            last_error = exc
            if exc.errno == errno_module.EADDRINUSE:
                raise GatewayStartupError(
                    _format_addrinuse_error(host, port),
                    errno=exc.errno,
                    host=host,
                    port=port,
                ) from exc
            if exc.errno == errno_module.EACCES:
                raise GatewayStartupError(
                    _format_eacces_error(host, port),
                    errno=exc.errno,
                    host=host,
                    port=port,
                ) from exc
            raise GatewayStartupError(
                f"Cannot bind to {host}:{port}: {exc}",
                errno=exc.errno,
                host=host,
                port=port,
            ) from exc
        finally:
            sock.close()

    # If no address family succeeded but no specific error was raised,
    # report a generic failure.
    if last_error is not None:
        raise GatewayStartupError(
            f"Cannot bind to {host}:{port}: {last_error}",
            errno=last_error.errno,
            host=host,
            port=port,
        ) from last_error


def _convert_oserror_to_startup_error(exc: OSError, host: str, port: int) -> GatewayStartupError:
    """Convert an OSError from web.run_app into a GatewayStartupError."""
    if exc.errno == errno_module.EADDRINUSE:
        return GatewayStartupError(
            _format_addrinuse_error(host, port),
            errno=exc.errno,
            host=host,
            port=port,
        )
    if exc.errno == errno_module.EACCES:
        return GatewayStartupError(
            _format_eacces_error(host, port),
            errno=exc.errno,
            host=host,
            port=port,
        )
    return GatewayStartupError(
        f"Cannot bind to {host}:{port}: {exc}",
        errno=exc.errno,
        host=host,
        port=port,
    )


def run_proxy(
    host: str = "127.0.0.1",
    port: int = 8877,
    db_path: str = "harness_evaluator_gateway.db",
    upstream_overrides: dict[Provider, str] | None = None,
    verify_ssl: bool = True,
) -> None:
    """Run the gateway proxy server.

    Raises ``GatewayStartupError`` if the port is already in use,
    requires elevated privileges, or is otherwise unavailable.
    The caller (typically the CLI) should catch this and present a
    user-friendly error message.
    """
    _check_port_available(host, port)
    store = CallStore(db_path)
    app, proxy = create_proxy_app(
        store, upstream_overrides, verify_ssl=verify_ssl
    )
    try:
        web.run_app(app, host=host, port=port, print=lambda msg: logger.info(msg))
    except OSError as exc:
        # Race condition: port was grabbed between the preflight check
        # and web.run_app's actual bind. Convert to GatewayStartupError
        # so the caller gets the same friendly message.
        raise _convert_oserror_to_startup_error(exc, host, port) from exc
