---
title: Architecture
description: How harness-evaluator's components interact and data flows through the system from config to results.
---

# Architecture

harness-evaluator is a Python core that orchestrates Node.js coding harnesses running inside Docker containers, with all provider traffic routed through a custom aiohttp gateway proxy for token/cost accounting.

## Component map

```
                            ┌─────────────────────────────────────────────────┐
                            │                   CLI (Typer)                    │
                            │  harness-evaluator run | gateway | canary | report | stats  │
                            │  harness-evaluator results | adapters | dashboard | calibrate│
                            └────────┬────────────────────────────────────────┘
                                     │
                    ┌────────────────┼─────────────────┐
                    ▼                ▼                 ▼
           ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
           │  Orchestrator │  │   Gateway   │  │  Dashboard   │
           │   (engine.py) │  │   Proxy     │  │  (FastAPI)   │
           │               │  │ (proxy.py)  │  │              │
           │ • Matrix      │  │             │  │ • Run list   │
           │ • Budget      │  │ • SSE parse │  │ • Leaderboard│
           │ • Retry       │  │ • Token cap │  │ • Filtering  │
           │ • Resume      │  │ • Cost calc │  │ • REST API   │
           └───────┬───────┘  └──────┬──────┘  └──────┬───────┘
                   │                 │                │
                   ▼                 │                │
           ┌──────────────┐          │                │
           │ Docker Runner│          │                │
           │ (docker.py)  │          │                │
           │              │          │                │
           │ • Container  │    ┌─────┴──────┐        │
           │ • Adapter    │───►│  Provider  │        │
           │   exec       │    │  API (HTTPS)│       │
           │ • Timeout    │◄───│            │        │
           └───────┬──────┘    └────────────┘        │
                   │                                 │
                   ▼                                 │
           ┌──────────────┐                          │
           │  Evaluator   │                          │
           │              │                          │
           │ • SWE tests  │                          │
           │ • LLM judge  │                          │
           │ • Error class│                          │
           └───────┬──────┘                          │
                   │                                 │
                   ▼                                 │
           ┌──────────────┐    ┌──────────────┐     │
           │ Results Store│◄───│ Gateway Store│     │
           │ (SQLite)     │    │ (SQLite)     │◄────┘
           │              │    │              │
           │ • run_results│    │ • captured_  │
           │ • run_state  │    │   calls      │
           │ • run_meta   │    │              │
           └──────┬───────┘    └──────────────┘
                  │
                  ▼
           ┌──────────────┐    ┌──────────────┐
           │  Reporting   │    │  Statistics  │
           │ (HTML/JSON/  │    │ (mixed-eff,  │
           │  CSV)        │    │  bootstrap)  │
           └──────────────┘    └──────────────┘
```

## Data flow: a single eval cell

The following traces the lifecycle of one cell in the eval matrix (one harness × model × task × repeat combination):

```
1. RunConfig.from_yaml()
   │  Parse YAML → HarnessSpec, ModelSpec, TaskSpec
   │
2. Orchestrator.run()
   │  build_matrix() → list[RunCell]
   │  Filter out completed cells (resumability)
   │  For each pending cell:
   │
3. ├── Budget reservation (asyncio.Lock)
   │   │  Estimate cost → subtract from remaining budget
   │   │  If insufficient → skip cell
   │   │
4. ├── DockerRunner.run_cell(cell)
   │   │
   │   ├── _clone_repo()
   │   │   Copy task repo to host workdir, git init
   │   │
   │   ├── Delete prior gateway calls for this trace_id
   │   │   (prevents double-counting on re-runs)
   │   │
   │   ├── _run_harness()
   │   │   │
   │   │   ├── create_adapter(harness.adapter)
   │   │   │   Load adapter from registry
   │   │   │
   │   │   ├── adapter.get_env()
   │   │   │   Build allowlisted env: API key, gateway URL, trace_id
   │   │   │
   │   │   ├── adapter.get_command(task_prompt)
   │   │   │   Build CLI command for the harness
   │   │   │
   │   │   ├── docker run -d --cap-drop=ALL ...
   │   │   │   Launch container with workdir mounted at /workspace
   │   │   │
   │   │   ├── docker exec: bash setup.sh (if present)
   │   │   │
   │   │   ├── docker exec: <harness command>
   │   │   │   │
   │   │   │   │  Harness makes API calls:
   │   │   │   │  ┌──────────────────────────────────────────┐
   │   │   │   │  │  Container                                │
   │   │   │   │  │  Harness → http://host.docker.internal:8877
   │   │   │   │  │                    │                      │
   │   │   │   │  │  ┌─────────────────┴────────────────┐    │
   │   │   │   │  │  │     Gateway Proxy (aiohttp)       │    │
   │   │   │   │  │  │  • Detect provider from path      │    │
   │   │   │   │  │  │  • Forward to real API (HTTPS)    │────┼──► Provider
   │   │   │   │  │  │  • Parse SSE/JSON for token usage │◄───┼──◄ API
   │   │   │   │  │  │  • Calculate cost                 │    │
   │   │   │   │  │  │  • Save CapturedCall to SQLite    │    │
   │   │   │   │  │  │  • Return response to harness     │    │
   │   │   │   │  │  └──────────────────────────────────┘    │
   │   │   │   │  └──────────────────────────────────────────┘
   │   │   │   │
   │   │   ├── docker stop (cleanup)
   │   │   └── _commit_changes() (stage + git commit on host)
   │   │
   │   ├── Evaluate on host:
   │   │   ├── SWE track: apply hidden test patch → run tests → parse results
   │   │   └── Open track: structural checks → LLM judge → composite score
   │   │
   │   └── Collect token usage from gateway (by trace_id)
   │       Sum all CapturedCall.usage + cost for this cell
   │
5. ├── Orchestrator reconciles budget
   │   Refund difference if cell cost < reserved
   │
6. ├── ResultsStore.save_result()
   │   Write to run_results table (SQLite)
   │   Set cell state to "completed"
   │
7. └── Update progress counters
       completed++ or failed++, total_cost += cell_cost
```

## Component responsibilities

### CLI (`cli.py`)

Typer-based entry point. Each command is a thin wrapper that instantiates the appropriate component and delegates. Commands: `run`, `gateway`, `canary`, `report`, `results`, `adapters`, `stats`, `dashboard`, `calibrate`. See [CLI Reference](cli-reference/).

### Gateway Proxy (`gateway/`)

Custom HTTP/SSE proxy that intercepts all provider API calls. Detects the provider from the API path, forwards requests over HTTPS, parses streaming and non-streaming responses for token usage, calculates cost, and saves everything to SQLite. See [Gateway Proxy](gateway-proxy/) for full details.

### Orchestrator (`orchestrator/`)

Builds the eval matrix (harness × model × task × repeat), manages budget caps with atomic reserve-and-reconcile, handles cell-level resumability, and retries transient failures with exponential backoff. See [Orchestrator](orchestrator/).

### Docker Runner (`runner/`)

Executes each eval cell in an isolated Docker container. Clones the task repo on the host, mounts it into the container, runs the harness via `docker exec`, then evaluates results on the host. Containers are launched with `--cap-drop=ALL` and run as a non-root user. See [Docker Runner](docker-runner/).

### Adapters (`adapters/`)

Per-harness wrappers that provide a uniform interface: `prepare()`, `run()`, `get_command()`, `get_env()`, `cleanup()`. The registry lazy-loads adapters by name. Each adapter sets gateway proxy env vars and API keys via an allowlist. See [Adapters](adapters/).

### Evaluators (`evaluator/`)

Two tracks:

- **SWE** (`swe.py`): applies a hidden test patch, runs the test command, parses pytest/unittest output, computes partial credit, and classifies errors (success, partial, overfit, timeout, refusal, wrong_approach, crash, no_change).
- **Open-ended** (`open_ended.py`): runs structural checks (file existence, syntax, tests), then a frozen LLM judge scores against a weighted rubric. Structural failures cap the composite score at 0.5. See [Evaluators](evaluators/).

### Results Store (`orchestrator/results_store.py`)

SQLite-backed store with three tables:

- `run_results` — per-cell metrics (tokens, cost, latency, success, error class, diff, test output)
- `run_state` — cell execution state (pending, running, completed, failed, skipped) for resumability and live progress
- `run_metadata` — full run config JSON, harness-evaluator version, Docker image for reproducibility

### Reporting (`reporting/`)

Generates static HTML, JSON, and CSV reports with within-model leaderboards. HTML uses Jinja2 with autoescaping to prevent stored XSS. See [Reporting](reporting/).

### Dashboard (`dashboard/`)

FastAPI web app with Jinja2 templates. Shows run overviews, leaderboards, filtered/paginated results tables, and live progress from `run_state`. Exposes REST API endpoints. No auth — localhost only. See [Reporting](reporting/).

### Statistics (`stats/`)

Mixed-effects model (`success ~ C(harness) + C(model) + (1|task)`), variance decomposition (harness/model/task/residual), bootstrap confidence intervals, and per-combination consistency analysis. See [Statistics](statistics/).

## Two SQLite databases

harness-evaluator uses two separate SQLite databases:

| Database | Default path | Contents |
|----------|-------------|----------|
| Gateway DB | `harness_evaluator_gateway.db` | `captured_calls` table — every provider API call with full token/cost/latency data |
| Results DB | `harness_evaluator_results.db` | `run_results`, `run_state`, `run_metadata` tables — per-cell eval results and run state |

The gateway DB is written to by the proxy and read by the Docker runner (to aggregate per-cell token usage via `trace_id`). The results DB is written to by the orchestrator and read by reports, dashboard, and stats.

## Trace ID propagation

Every eval cell gets a unique `trace_id` (the `cell_id`: `{harness}__{model}__{task}__r{repeat}`). This ID flows through the system:

1. **Orchestrator** → passes `cell.cell_id` as `trace_id` to the Docker runner
2. **Docker runner** → passes `trace_id` to the adapter constructor
3. **Adapter** → appends `?trace_id=<cell_id>` to the gateway URL in `get_env()`
4. **Proxy** → extracts `trace_id` from the query string or `x-harness-evaluator-trace-id` header, stores it with each `CapturedCall`
5. **Docker runner** → after harness execution, queries the gateway store for all calls with this `trace_id` to aggregate token usage and cost

This ensures accurate per-cell cost attribution even when a harness makes dozens of API calls during a single run.

## Exit classes

Every run is classified into one of four exit classes:

| Exit class | Meaning | Treatment in stats |
|------------|---------|--------------------|
| `pass` | Task succeeded (tests pass / judge approves) | Counted as success (1.0) |
| `fail` | Task failed, non-retryable | Counted as failure (0.0) |
| `retryable_kill` | Transient issue (rate limit, OOM, timeout) | Counted as failure (0.0) |
| `non_retryable_kill` | Non-transient issue (harness crash, config error) | Counted as failure (0.0) |

All exit classes enter the effectiveness significance tests. Kills are recorded as `success=0.0`. The exit class is preserved in the results database for later reliability analysis, but the current statistics module does not filter by exit class.

## Security model

- **Container isolation**: `--cap-drop=ALL` removes all Linux capabilities. Harnesses only need file I/O and network access.
- **Non-root execution**: containers run as the `harness-evaluator` user (UID created in Dockerfile).
- **Env allowlist**: adapters pass only a minimal set of env vars to containers (PATH, HOME, USER, SHELL, LANG, etc.) plus the gateway URL and API key — never the whole host environment.
- **Header redaction**: the proxy redacts sensitive headers (auth, API keys, cookies, tokens) before storing to SQLite, using both an explicit list and a substring heuristic.
- **Trace header stripping**: internal trace headers (`x-harness-evaluator-trace-id`, `x-trace-id`) and the `trace_id` query param are never forwarded to the real provider API.
- **Path traversal prevention**: identifiers (run names, harness names, model names) are validated against `[A-Za-z0-9._-]+` and sanitized before use in file paths and container names.
- **No dashboard auth**: the dashboard has no authentication — keep it localhost-only.
