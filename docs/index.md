---
title: harness-evaluator Documentation
description: Comprehensive documentation for harness-evaluator — a harness evaluator that compares agentic coding harnesses on token efficiency, task effectiveness, and time efficiency.
---

# harness-evaluator Documentation

**harness-evaluator** (harness evaluator) compares agentic coding harnesses — Claude Code, Codex, Pi, OpenCode, OMP, Aider, Gemini CLI, Antigravity, Copilot, Cursor, and Kiro — against one or more models on a set of tasks. It measures which harnesses are most token-efficient, task-effective, and time-effective.

The comparison is **product-level**: which harness to *use* with a given model. The harness's system prompt, tool set, context strategy, and safety policy are part of what is being evaluated, not controlled for.

## How harness-evaluator works

```
 ┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌────────────┐
 │  Run Config  │────►│ Orchestrator │────►│ Docker Runner │────►│  Evaluator │
 │  (YAML)      │     │ (matrix,     │     │ (container    │     │ (SWE /     │
 │              │     │  budget,     │     │  per cell,    │     │  open-ended)│
 │              │     │  retry)      │     │  adapter exec)│     │            │
 └─────────────┘     └──────┬───────┘     └───────┬───────┘     └─────┬──────┘
                            │                     │                   │
                            ▼                     ▼                   ▼
                     ┌──────────────┐     ┌───────────────┐     ┌────────────┐
                     │ Results Store│◄────│ Gateway Proxy │     │  Reports   │
                     │ (SQLite)     │     │ (token/cost   │     │ (HTML/JSON │
                     │              │     │  capture)     │     │  /CSV)     │
                     └──────────────┘     └───────────────┘     └────────────┘
                            │                                           │
                            ▼                                           ▼
                     ┌──────────────┐     ┌───────────────────────────────────┐
                     │  Dashboard   │     │  Statistics                        │
                     │  (FastAPI)   │     │  (mixed-effects, bootstrap CIs)    │
                     └──────────────┘     └───────────────────────────────────┘
```

## Documentation sections

| Section | Description |
|---------|-------------|
| [Getting Started](getting-started/) | Step-by-step: install, build Docker, set keys, run your first eval |
| [Architecture](architecture/) | High-level architecture, component interactions, data flow |
| [Gateway Proxy](gateway-proxy/) | HTTP/SSE proxy that captures token usage, cost, and latency |
| [Orchestrator](orchestrator/) | Eval matrix building, budget caps, retry logic, resumability |
| [Docker Runner](docker-runner/) | Container isolation, security hardening, harness execution |
| [Evaluators](evaluators/) | SWE-bench-style hidden tests and open-ended LLM judge track |
| [Adapters](adapters/) | Adapter system, registry, per-harness details, observability tiers |
| [CLI Reference](cli-reference/) | All CLI commands, flags, and options with examples |
| [Configuration](configuration/) | Run YAML config, task definitions, pricing tables, env vars |
| [Guides](guides/multi-phase/) | Multi-phase evaluation with adversarial review |
| [Subscription Auth](guides/subscription/) | Running with Claude Code OAuth / Codex ChatGPT subscriptions |
| [Reporting](reporting/) | Static reports (HTML/JSON/CSV), interactive dashboard |
| [Statistics](statistics/) | Mixed-effects models, variance decomposition, bootstrap CIs |
| [Development](development/) | Contributing, quality gates, code style, testing, conventions |

## Key concepts

### Comparison model

- **Within-model**: fix the model, vary the harness — "Which harness gets the most out of Sonnet?"
- **Within-harness**: fix the harness, vary the model — "Which model does OpenCode drive best?"
- **No cross-model Pareto**: token accounting is non-fungible across vendors, so cross-model Pareto fronts are not meaningful.

The harness × model matrix is sparse (vendor-locked harnesses). Only viable cells are run.

### Observability tiers

Every result row carries an `observability_tier`:

| Tier | Description |
|------|-------------|
| `full` | Open/cooperating harness — system prompts, tools, context strategy, sub-agent attribution all available |
| `partial` | Closed harness but provider traffic captured through the gateway proxy |
| `minimal` | Only total spend or billing data available; traffic may bypass the proxy |

Leaderboards can be filtered by tier. Comparisons across tiers are flagged.

### Task tracks

harness-evaluator supports three tracks with separate leaderboards (never cross-compared):

- **SWE-bench-style** (`swe`): repo + issue + hidden test patch. Objective pass/fail with partial credit.
- **Open-ended** (`open_ended`): free-form tasks evaluated by a frozen LLM judge with a structured rubric and structural checks.
- **Multi-phase** (`multi_phase`): sequential phases (implement → review → revise) with an adversarial reviewer model. Evaluated with hidden tests after all phases complete. See the [Multi-phase evaluation guide](guides/multi-phase/).

### Metrics

| Metric | Definition |
|--------|------------|
| Effectiveness | Success rate with partial credit + error classification |
| Token efficiency | Input/output/cache-read/cache-write/reasoning tokens per task |
| Time efficiency | Wall-clock to completion + time to first solution attempt |
| Cost efficiency | $ per success (includes failed runs) |
| Robustness | Variance across repeats, error recovery, failure modes |
| Transparency | Gateway vs self-report token discrepancy |

## Project layout

```
harness-evaluator/
├── src/harness_evaluator/
│   ├── cli.py                  # Typer-based CLI entry point
│   ├── gateway/                # HTTP/SSE proxy, parsers, SQLite store, reconciliation
│   │   ├── proxy.py            # aiohttp proxy server
│   │   ├── models.py           # CapturedCall, TokenUsage, PricingTable
│   │   ├── store.py            # SQLite-backed call storage
│   │   ├── canary.py           # Proxy accuracy verification
│   │   ├── reconcile.py        # Multi-source token reconciliation
│   │   └── parsers/            # Per-provider SSE/JSON usage parsers
│   ├── orchestrator/           # Matrix builder, budget engine, results store
│   │   ├── config.py           # RunConfig, TaskSpec, HarnessSpec, ModelSpec
│   │   ├── engine.py           # Orchestrator with retry + budget logic
│   │   └── results_store.py    # SQLite results storage
│   ├── runner/
│   │   └── docker.py           # Docker container lifecycle
│   ├── adapters/               # Per-harness CLI wrappers
│   │   ├── base.py             # BaseAdapter, AdapterInfo, AdapterResult
│   │   ├── registry.py         # Adapter registry (lazy-loaded)
│   │   ├── claude_code.py      # Claude Code adapter
│   │   ├── codex.py            # Codex adapter
│   │   ├── opencode.py         # OpenCode adapter
│   │   ├── aider.py            # Aider adapter
│   │   ├── gemini.py           # Gemini CLI adapter
│   │   ├── antigravity.py      # Antigravity CLI adapter
│   │   ├── pi.py               # Pi adapter
│   │   ├── omp.py              # OMP adapter
│   │   ├── copilot.py          # GitHub Copilot CLI adapter
│   │   ├── cursor.py           # Cursor CLI adapter
│   │   └── kiro.py             # Kiro CLI adapter
│   ├── evaluator/              # SWE hidden-test + open-ended LLM judge tracks
│   │   ├── swe.py              # SWEEvaluator with error classification
│   │   └── open_ended.py       # FrozenJudge, Rubric, StructuralChecker, calibration
│   ├── dashboard/
│   │   ├── app.py              # FastAPI dashboard with Jinja2 templates
│   │   └── templates/          # _base.html, index.html, run_detail.html, cell_detail.html
│   ├── reporting/
│   │   └── static_report.py    # HTML/JSON/CSV report generator
│   └── stats/
│       └── __init__.py         # Mixed-effects model, variance decomposition, bootstrap
├── tasks/                      # Task YAML definitions and repo fixtures
├── runs/                       # Sample run config YAMLs
├── tests/                      # pytest test suite
├── docs/                       # This documentation
├── Dockerfile                  # Image with all 11 harnesses (node:22-slim base)
└── pyproject.toml              # Dependencies, ruff/mypy/pytest config
```
