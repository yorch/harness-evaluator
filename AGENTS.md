# AGENTS.md — Project conventions for heval

## Build & Quality Gates

```bash
# Install dependencies
uv sync --extra dev

# Lint
uv run ruff check src/ tests/

# Type check
uv run mypy src/heval/

# Tests
uv run pytest tests/ -v

# All gates at once
uv run ruff check src/ tests/ && uv run mypy src/heval/ && uv run pytest tests/ -q
```

All three gates (ruff, mypy, pytest) must pass before completing any milestone.

## Architecture

- Python core (orchestrator, gateway, evaluator, CLI, dashboard)
- TS adapters per harness where native integration matters
- Custom HTTP/SSE proxy for token accounting (aiohttp-based)
- SQLite for captured call storage
- Docker for run isolation

## Key Modules

- `heval.gateway.proxy` — HTTP/SSE proxy server
- `heval.gateway.parsers.anthropic` — Anthropic SSE/JSON usage parser
- `heval.gateway.parsers.openai` — OpenAI SSE/JSON usage parser
- `heval.gateway.models` — Data models (TokenUsage, CapturedCall, PricingTable)
- `heval.gateway.store` — SQLite-backed call storage
- `heval.gateway.reconcile` — Multi-source token reconciliation
- `heval.gateway.canary` — Proxy accuracy verification
- `heval.orchestrator.config` — Task/run config models, matrix builder
- `heval.orchestrator.engine` — Orchestrator with retry, budget, resumability
- `heval.orchestrator.results_store` — SQLite results store
- `heval.evaluator.swe` — SWE-bench-style evaluator with partial credit
- `heval.evaluator.open_ended` — Frozen judge, rubric, structural checks, calibration
- `heval.runner.docker` — Docker-based task runner (placeholder for M3)
- `heval.reporting.static_report` — HTML/JSON/CSV report generator
- `heval.dashboard.app` — FastAPI interactive dashboard
- `heval.stats` — Mixed-effects model, variance decomposition, bootstrap CIs, consistency
- `heval.adapters.base` — Base adapter class and adapter info
- `heval.adapters.registry` — Adapter registry (load by name)
- `heval.adapters.opencode` — OpenCode adapter (full observability)
- `heval.adapters.claude_code` — Claude Code adapter (partial)
- `heval.adapters.codex` — Codex adapter (partial)
- `heval.adapters.pi` — Pi adapter (minimal)
- `heval.adapters.omp` — OMP adapter (minimal)
- `heval.cli` — CLI entry point (typer-based)

## Code Style

- Line length: 100 chars
- Ruff rules: E, F, W, I, UP, B, SIM, C4
- Strict mypy
- pytest-asyncio with auto mode
