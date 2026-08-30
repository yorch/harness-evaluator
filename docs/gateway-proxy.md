---
title: Gateway Proxy
description: Custom HTTP/SSE proxy that intercepts provider API calls for token, cost, and latency accounting.
---

# Gateway Proxy

The gateway proxy is a custom HTTP/SSE server that sits between an agentic
coding harness and the real provider API (Anthropic, OpenAI). It transparently
forwards all traffic while capturing token usage, cost, and latency for every
API call — without requiring any modification to the harness itself.

No TLS interception is needed. The harness talks plain HTTP to `localhost`,
and the proxy talks HTTPS to the real provider with full certificate
verification.

## High-level architecture

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                        Docker Container                          │
 │                                                                  │
 │   Harness (Claude Code, Codex, OpenCode, Pi, OMP)               │
 │       │                                                          │
 │       │  HTTP request to host.docker.internal:8877               │
 │       │  (ANTHROPIC_BASE_URL / OPENAI_BASE_URL → proxy)          │
 │       │  ?trace_id=<cell-trace-id> appended by adapter           │
 │       ▼                                                          │
 │     ┌────────────────────────────────────────────┐               │
 │     │          GatewayProxy (aiohttp)             │               │
 │     │                                            │               │
 │     │  1. Detect provider from API path           │               │
 │     │     /v1/messages        → Anthropic         │               │
 │     │     /v1/chat/completions → OpenAI           │               │
 │     │                                            │               │
 │     │  2. Read & parse request body               │               │
 │     │     Extract: model, stream flag, trace_id   │               │
 │     │                                            │               │
 │     │  3. Strip hop-by-hop & trace headers        │               │
 │     │     Keep: Authorization, Content-Type       │               │
 │     │     Strip: x-heval-trace-id, trace_id param │               │
 │     │                                            │               │
 │     │  4. Forward to upstream over HTTPS ─────────┼──►  Real Provider API
 │     │                                            │       (api.anthropic.com
 │     │  5. Receive response (SSE or JSON) ◄────────┼──◄    api.openai.com)
 │     │                                            │               │
 │     │  6. Parse token usage (provider-specific)   │               │
 │     │     Anthropic: message_delta SSE events     │               │
 │     │     OpenAI:    chunk.usage on last chunk    │               │
 │     │                                            │               │
 │     │  7. Calculate cost via pricing table        │               │
 │     │     get_pricing_strict(model) → warns on    │               │
 │     │     unknown models (no silent $0)           │               │
 │     │                                            │               │
 │     │  8. Save CapturedCall to SQLite ────────────┼──►  heval_gateway.db
 │     │     (offloaded via asyncio.to_thread)       │               │
 │     │                                            │               │
 │     │  9. Return response to harness ◄────────────│               │
 │     └────────────────────────────────────────────┘               │
 │       │                                                          │
 │       ▼                                                          │
 │   Harness receives response transparently                        │
 │   (identical to talking to the provider directly)                │
 └──────────────────────────────────────────────────────────────────┘
```

## How it works

### 1. Provider detection

The proxy detects which provider to forward to based on the API path:

| Path prefix              | Provider   |
|--------------------------|------------|
| `/v1/messages`           | Anthropic  |
| `/v1/chat/completions`   | OpenAI     |
| `/v1/responses`          | OpenAI     |

Unknown paths return a `404` with a JSON error.

### 2. Request forwarding

The proxy reads the request body, extracts the model name and streaming flag,
then forwards the request to the real provider API over HTTPS. It:

- **Keeps** auth headers (`Authorization`, `x-api-key`) and `Content-Type`
- **Strips** hop-by-hop headers (`Connection`, `Transfer-Encoding`, etc.)
- **Strips** internal trace headers (`x-heval-trace-id`) and the `trace_id`
  query parameter so they never reach the real provider
- **Sets** the `Host` header to the upstream provider's domain

### 3. Trace attribution

Each eval cell is assigned a unique `trace_id`. The Docker runner passes this
to the harness via environment variables, and the adapter appends
`?trace_id=<cell-id>` to the gateway URL. The proxy extracts it from either
the `x-heval-trace-id` header or the query string, and stores it with the
`CapturedCall` so calls can be attributed back to specific eval cells.

### 4. Non-streaming responses

For standard JSON responses (e.g. `stream: false`):

1. Read the full response body
2. Parse token usage from the JSON via provider-specific parsers
3. Calculate cost using `get_pricing_strict(model)`
4. Save a `CapturedCall` to SQLite (offloaded to a thread to avoid blocking
   the event loop)
5. Return the response body and headers to the harness

### 5. Streaming (SSE) responses

For Server-Sent Events responses (e.g. `stream: true`):

1. Create a `StreamResponse` and start writing chunks to the harness
   **immediately** — zero added latency
2. Buffer raw bytes and split on `\n` boundaries to avoid corrupting SSE
   events at chunk boundaries
3. Process each complete SSE line through provider-specific parsers to
   accumulate token usage in real-time
4. Per-stream event state (`current_event`) is kept as a **local variable**,
   not shared on the proxy instance, so concurrent streams don't overwrite
   each other
5. Cap stored SSE text at 100 KB to avoid unbounded memory growth
6. On stream errors (client disconnect, payload error), record the error but
   still save whatever was captured

### 6. Token usage parsing

The proxy uses provider-specific parsers:

**Anthropic** (`heval.gateway.parsers.anthropic`):
- SSE: parses `event: message_delta` and `data: {...}` lines to extract
  `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and
  `cache_read_input_tokens`
- Non-streaming: reads `usage` from the response JSON

**OpenAI** (`heval.gateway.parsers.openai`):
- SSE: parses the final chunk's `usage` object (OpenAI sends usage on the
  last chunk when `stream_options.include_usage` is set)
- Non-streaming: reads `usage` from the response JSON

Both parsers produce a `TokenUsage` object with:
- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `total_tokens` (computed)

### 7. Cost calculation

Cost is calculated using `get_pricing_strict(model)` from
`heval.gateway.models`. If the model is not in the pricing table, a warning is
logged and zero-cost is returned — but the warning ensures unknown models are
visible rather than silently bypassing budget accounting.

The pricing table maps each model to per-token rates (input, output,
cache-read, cache-write) and the `Pricing.calculate()` method multiplies
token counts by the corresponding rate.

### 8. Storage

Each captured call is saved as a `CapturedCall` record in SQLite
(`heval_gateway.db`) with:

| Field             | Description                                              |
|-------------------|----------------------------------------------------------|
| `id`              | UUID for the call                                        |
| `trace_id`        | Links the call to a specific eval cell                   |
| `provider`        | `anthropic` or `openai`                                  |
| `model`           | Model name from the request body                         |
| `method`          | HTTP method (`POST`, etc.)                               |
| `path`            | API path (`/v1/messages`, etc.)                          |
| `request_headers` | Redacted headers (auth/keys/cookies stripped)            |
| `request_body`    | Full request JSON (capped at 10 MB)                      |
| `response_status` | HTTP status code (0 on upstream errors)                  |
| `response_headers`| Redacted response headers                                |
| `response_body`   | Full response JSON or SSE summary (capped)               |
| `usage`           | `TokenUsage` with all token counts                       |
| `cost`            | Calculated cost (input + output + cache components)      |
| `latency_ms`      | Wall-clock latency from request to response completion   |
| `is_streaming`    | Whether the call used SSE                                |
| `error`           | Error message if the call failed                         |

### 9. Sensitive header redaction

Headers are redacted before storage using both:

- **Explicit list**: `authorization`, `x-api-key`, `cookie`, `set-cookie`,
  `proxy-authorization`, `x-amz-security-token`, `x-auth-token`,
  `x-session-token`, `x-access-token`, `api-key`, `openai-organization`,
  `anthropic-organization`
- **Substring heuristic**: any header name containing `key`, `token`,
  `secret`, `auth`, `cookie`, or `password` (case-insensitive)

Redacted headers are replaced with `[REDACTED]`.

### 10. Error handling

| Error type                | HTTP status | Behavior                                    |
|---------------------------|-------------|---------------------------------------------|
| `aiohttp.ClientError`     | 502         | Saves call with `response_status=0` + error |
| `TimeoutError` (300s)     | 504         | Saves call with timeout error message       |
| Stream interrupted        | (stream)    | Saves partial capture with error            |
| Unknown API path          | 404         | Returns JSON error, no capture              |
| Request body too large    | 413         | Returns error, no forwarding                |

### 11. Session management

The proxy lazily creates an `aiohttp.ClientSession` guarded by
`asyncio.Lock` to prevent concurrent creation races. The session is reused
across requests and cleaned up on application shutdown.

TLS verification is enabled by default. It can be disabled via the
`verify_ssl` parameter for testing environments.

## Running the proxy

```bash
# Start the gateway proxy on port 8877
harnessbench gateway --port 8877

# Or with a custom host and database path
harnessbench gateway --host 0.0.0.0 --port 8877 --db ./heval_gateway.db
```

## How harnesses connect

The Docker runner sets environment variables inside the container so the
harness routes through the proxy instead of the real provider:

```bash
# Inside the Docker container
ANTHROPIC_BASE_URL=http://host.docker.internal:8877
OPENAI_BASE_URL=http://host.docker.internal:8877/v1
```

For OpenAI, the adapter appends `/v1` to the path so the base URL ends with
`/v1` (the proxy routes `/v1/chat/completions` and `/v1/responses`).
A `trace_id` query parameter is also appended for trace propagation.

The harness then makes normal API calls, which hit the proxy. The proxy
forwards them to the real provider with the original API key (passed through
from the host environment) and captures everything transparently.

## Canary verification

After sending a request through the proxy, verify token capture accuracy
against the provider's own usage reporting:

```bash
harnessbench canary --tolerance-pct 1.0
```

This sends a test request through the proxy and compares the proxy's captured
token counts against the provider's response. A tolerance of 1.0% allows for
minor rounding differences.

## Observability tiers

The proxy supports three observability tiers, determined by the harness
adapter:

| Tier     | Description                                              |
|----------|----------------------------------------------------------|
| `full`   | Open harness (e.g. OpenCode) — all metadata captured     |
| `partial`| Closed harness (e.g. Claude Code, Codex) — proxy         |
|          | captures provider traffic                                |
| `minimal`| Closed harness that may bypass proxy (e.g. Pi, OMP) —   |
|          | only total spend via billing API                         |

## Reconciliation

Token usage from three sources is reconciled with per-harness tolerance bands:

1. **Proxy capture** — what the gateway recorded
2. **Billing API** — the provider's own usage/billing endpoint
3. **Harness self-report** — usage reported by the harness itself

Discrepancies are flagged as a transparency metric in the final report.

## Configuration reference

CLI options for `harnessbench gateway`:

| Parameter  | Default            | Description                |
|------------|--------------------|----------------------------|
| `--host`   | `127.0.0.1`        | Bind address               |
| `--port`   | `8877`             | Listen port                |
| `--db`     | `heval_gateway.db` | SQLite database path       |

The proxy also accepts `verify_ssl` and `upstream_overrides` as keyword
arguments to `run_proxy()` (used programmatically by the orchestrator), but
these are not exposed as CLI flags.

## Key source files

| File                                  | Description                          |
|---------------------------------------|--------------------------------------|
| `src/heval/gateway/proxy.py`          | Proxy server and request handler     |
| `src/heval/gateway/models.py`         | `CapturedCall`, `TokenUsage`, pricing|
| `src/heval/gateway/store.py`          | SQLite-backed call storage           |
| `src/heval/gateway/parsers/anthropic.py` | Anthropic SSE/JSON usage parser   |
| `src/heval/gateway/parsers/openai.py` | OpenAI SSE/JSON usage parser         |
| `src/heval/gateway/canary.py`         | Proxy accuracy verification          |
| `src/heval/gateway/reconcile.py`      | Multi-source token reconciliation    |
