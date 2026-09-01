# harness-evaluator

[![CI](https://github.com/yorch/harness-evaluator/actions/workflows/ci.yml/badge.svg)](https://github.com/yorch/harness-evaluator/actions/workflows/ci.yml)
[![Docker](https://github.com/yorch/harness-evaluator/actions/workflows/docker.yml/badge.svg)](https://github.com/yorch/harness-evaluator/actions/workflows/docker.yml)
[![Docs Site](https://github.com/yorch/harness-evaluator/actions/workflows/astro.yml/badge.svg)](https://yorch.github.io/harness-evaluator/)
[![PyPI](https://img.shields.io/pypi/v/harness-evaluator?logo=pypi&color=blue)](https://pypi.org/project/harness-evaluator/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Harness evaluator: compare agentic coding harnesses (Claude Code, Codex, Pi,
OpenCode, OMP, Aider, Gemini CLI, Antigravity, Copilot, Cursor, Kiro) on token
efficiency, task effectiveness, and time efficiency.

See [DESIGN.md](DESIGN.md) for the full design specification and
[the docs site](https://yorch.github.io/harness-evaluator/) for comprehensive documentation.

## Quick start

No clone required — harness-evaluator bundles its task library and publishes to PyPI as `harness-evaluator`:

```bash
uvx harness-evaluator init                            # scaffold harness-evaluator.yaml
docker pull ghcr.io/yorch/harness-evaluator-runner:latest   # pull the runner image
export ANTHROPIC_API_KEY=sk-ant-...
uvx harness-evaluator gateway --port 8877             # separate terminal
uvx harness-evaluator run harness-evaluator.yaml
```

Install `uv` first if you don't have it: `curl -LsSf https://astral.sh/uv/install.sh | sh`

You can also install with `pip install harness-evaluator` or `uv tool install harness-evaluator`.

### From source

```bash
# 1. Install dependencies
uv sync --extra dev

# 2. Pull the pre-built Docker image (or build locally with: docker build -t harness-evaluator-runner:latest .)
docker pull ghcr.io/yorch/harness-evaluator-runner:latest

# 3. Set API keys
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# 3a. Confirm the keys work before spending anything on a run
harness-evaluator check-keys

# 4. Start the gateway proxy (in a separate terminal)
harness-evaluator gateway --port 8877

# 5. Run a minimal evaluation (1 harness, 1 model, 1 task, 1 repeat)
harness-evaluator run runs/sample-minimal.yaml

# Or dry-run to see the matrix without executing
harness-evaluator run runs/sample-minimal.yaml --dry-run

# Curated 20-task mix (5 harnesses × 2 models × 20 tasks × 3 repeats = 600 cells)
harness-evaluator run runs/task-mix.yaml --dry-run
```

## Docker image

The runner executes harnesses inside an isolated Docker container. A pre-built
image is available on GHCR (recommended), or you can build locally:

```bash
# Pull the pre-built image (recommended)
docker pull ghcr.io/yorch/harness-evaluator-runner:latest

# Or build locally
docker build -t harness-evaluator-runner:latest .
```

## Running with a subscription (Claude Code OAuth / Codex ChatGPT)

By default, harness-evaluator authenticates with pay-per-token API keys. Both
Claude Code and Codex also support subscription-based access — Claude Code via
OAuth (Claude Pro/Max) and Codex via a ChatGPT subscription. Token usage is
still captured through the gateway proxy for analysis, but cost is recorded as
`$0` and does not count against `budget_usd`.

Set `auth_mode`, `credentials_path`, and `cost_mode: subscription` on the model:

```yaml
# Claude Code on a Claude Pro/Max subscription
models:
  - name: claude-sonnet-5
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    auth_mode: claude_oauth
    credentials_path: "~/.claude/.credentials.json"
    cost_mode: subscription

# Codex on a ChatGPT subscription
models:
  - name: gpt-5
    provider: openai
    api_key_env: OPENAI_API_KEY
    auth_mode: codex_chatgpt
    credentials_path: "~/.codex/auth.json"
    cost_mode: subscription
```

The credential files are obtained by logging in to the harness CLI on the host
(`claude` for Claude Code, `codex login` for Codex). The Docker runner copies the
credential directory into the container (writable, so tokens can refresh) and
excludes it from the eval diff. See the
[Subscription auth guide](docs/guides/subscription.md) for the full walkthrough.

## M1: Gateway Proxy (completed)

The gateway proxy is a custom HTTP/SSE server that sits between a harness and
the provider API, capturing every call's token usage, cost, and latency.

See [docs/gateway-proxy.md](docs/gateway-proxy.md) for a detailed explanation
with architecture diagram, token parsing, storage schema, and configuration
reference.

### Running the proxy

```bash
harness-evaluator gateway --port 8877
```

Configure harnesses to route through the proxy:
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8877
export OPENAI_BASE_URL=http://127.0.0.1:8877
```

### Running the canary

After sending a request through the proxy, verify token capture accuracy:
```bash
harness-evaluator canary --tolerance 1.0
```

### What the proxy captures

- **Token usage**: input, output, cache-read, cache-write, reasoning tokens
- **Cost**: calculated from a pricing table per model
- **Latency**: wall-clock time per API call
- **Full request/response**: headers and bodies stored in SQLite
- **Streaming support**: real-time SSE parsing for both Anthropic and OpenAI

### Observability tiers

- **full**: open harness, all metadata captured
- **partial**: closed harness, provider traffic captured via proxy
- **minimal**: closed harness, only total spend via billing API

### Reconciliation

Token usage from proxy, billing API, and harness self-report are reconciled
with per-harness tolerance bands. Discrepancies are flagged as a transparency
metric.

## M2: Core Pipeline (completed)

The core pipeline handles eval matrix building, execution, and reporting.

### Running an eval

```bash
# Dry run (print the matrix without executing)
harness-evaluator run runs/sample-run.yaml --dry-run

# Execute the eval
harness-evaluator run runs/sample-run.yaml

# Generate reports
harness-evaluator report broad-first-pass --output ./reports

# View results in console
harness-evaluator results broad-first-pass
```

### Run configuration

Eval runs are configured via YAML files (see `runs/sample-run.yaml`):
- `harnesses`: list of harness specs (name, adapter, observability tier)
- `models`: list of model specs (name, provider, API key env var)
- `tasks`: list of task IDs or `*` for all tasks in the library
- `repeats`: number of repeats per cell (default 5)
- `budget_usd`: maximum total spend (optional)
- `parallel_runs`: number of parallel container runs (default 1)

### Task definitions

Tasks are defined as YAML files in a task library directory (see `tasks/`):
- `track`: `swe` (hidden tests) or `open_ended` (LLM judge)
- `task_prompt`: the prompt given to the harness
- `test_command`: command to run tests
- `test_patch`: hidden test patch applied before evaluation
- `timeout_seconds`: per-task timeout

### Features

- **Matrix building**: harness × model × task × repeat
- **Budget caps**: stops when $ budget exhausted
- **Cell-level resumability**: skips completed cells on re-run
- **Retry logic**: transient failures retried with exponential backoff
- **Exit classes**: PASS, FAIL, RETRYABLE_KILL, NON_RETRYABLE_KILL
- **Partial credit**: fraction of tests passing
- **Error classification**: success, partial, overfit, timeout, refusal, wrong_approach, crash, no_change
- **Reports**: HTML, JSON, CSV with within-model leaderboards

## M3: Harness Adapters (completed)

Adapters wrap each coding harness with a uniform interface for the runner.

### Supported harnesses

| Harness | Adapter | Observability | In default image? | Notes |
|---------|---------|--------------|-------------------|-------|
| OpenCode | `opencode` | full | Yes | Open-source, system prompt visible |
| Aider | `aider` | full | No | Open-source, multi-provider |
| Claude Code | `claude-code` | partial | Yes | Closed, proxy captures traffic |
| Codex | `codex` | partial | Yes | Closed, proxy captures traffic |
| Gemini CLI | `gemini` | partial | No | Google, proxy captures traffic |
| Antigravity | `antigravity` | partial | No | Google, proxy captures traffic |
| Pi | `pi` | minimal | Yes | May bypass proxy |
| OMP | `omp` | minimal | Yes | May bypass proxy |
| GitHub Copilot | `copilot` | minimal | No | GitHub, may bypass proxy |
| Cursor | `cursor` | minimal | No | Multi-provider, may bypass proxy |
| Kiro | `kiro` | minimal | No | AWS, may bypass proxy |

The default Docker image includes 5 harnesses (OpenCode, Claude Code, Codex,
Pi, OMP). The other 6 adapters are registered but require a custom Docker
image — see [Adapters](docs/adapters.md) for install commands.

### Listing adapters

```bash
harness-evaluator adapters
```

### Observability tiers

- **full**: Open/cooperating harness. System prompts, tool definitions, context
  strategy, and turn-level metadata are available.
- **partial**: Closed harness but provider traffic is captured through the
  gateway proxy. Token usage and cost are accurately attributed.
- **minimal**: Only total spend or billing data is available. Traffic may
  bypass the proxy. Cost accounting relies on billing reconciliation.

### Adapter design

Each adapter implements:
- `prepare()`: Check/install the harness
- `run(task_prompt, timeout)`: Execute the harness non-interactively
- `cleanup()`: Clean up after the run
- `get_env()`: Set gateway proxy env vars and API keys

The adapter registry (`harness_evaluator.adapters.registry`) loads adapters by name and
the Docker runner uses it to dispatch to the correct adapter based on the
run config's `harness.adapter` field.

## M4: Open-Ended Track (completed)

The open-ended track evaluates tasks without a single correct answer using a
frozen LLM judge, structured rubric, and structural checks.

### Components

- **Frozen Judge** (`FrozenJudge`): Versioned LLM judge with an immutable prompt.
  Version `v1.0` is the initial frozen prompt. Changing the prompt requires
  bumping the version, which invalidates prior calibration data.
- **Rubric** (`Rubric`): Weighted criteria with 0-5 scoring scale. Default
  rubric includes correctness (3x), completeness (2x), code_quality (1.5x),
  test_quality (1.5x), documentation (1x).
- **Structural Checks** (`StructuralChecker`): Verifies file existence, Python
  syntax, and test command execution. Structural failures cap the composite
  success at 0.5.
- **Calibration** (`CalibrationSet`): Anchor submissions with known expected
  scores for drift detection. Mean absolute error > 0.15 flags drift.

### Running calibration

```bash
export ANTHROPIC_API_KEY=sk-ant-...
harness-evaluator calibrate --model claude-sonnet-5
```

### Evaluation flow

1. Get git diff of changes
2. Run structural checks (file existence, syntax, tests)
3. Run LLM judge against rubric
4. Composite score: judge score, capped at 0.5 if structural checks fail
5. Pass threshold: 0.7

## M5: Dashboard (completed)

Interactive FastAPI web dashboard for exploring eval results.

### Starting the dashboard

```bash
harness-evaluator dashboard --port 8080
```

Then open http://127.0.0.1:8080 in your browser.

### Features

- **Run overview**: List all runs with summary stats (cells, passed, failed, cost)
- **Run detail**: Per-run view with leaderboards, filtered results table, failed/skipped cells section, and collapsible phase details
- **Cell detail**: Per-cell page with diff, test output, harness stdout/stderr, phase results, and reconciliation
- **Error visibility**: Error class and error message columns in results table; failed/skipped cells section with persisted error reasons
- **Filtering**: Filter by model, harness, task track, and minimum success rate
- **Sorting**: Click any column header to sort
- **Dark mode**: Automatic via `prefers-color-scheme` with manual toggle
- **Export**: Download filtered results as CSV or JSON
- **REST API**: JSON endpoints for programmatic access:
  - `GET /api/runs` — list all runs
  - `GET /api/run/{name}` — get filtered results
  - `GET /api/run/{name}/leaderboard` — get leaderboard data
  - `GET /api/run/{name}/status` — get live progress
  - `GET /api/run/{name}/errors` — get failed/skipped cells with errors

## M6: Statistics (completed)

Statistical analysis of evaluation results, including mixed-effects modeling,
variance decomposition, bootstrap confidence intervals, and consistency analysis.

### Running stats

```bash
harness-evaluator stats my-run --db harness_evaluator_results.db
```

### Components

- **Mixed-Effects Model**: `success ~ C(harness) + C(model) + (1|task)`
  Treats harness and model as fixed effects, task as a random effect.
  Reports coefficients, standard errors, p-values, and confidence intervals.
- **Variance Decomposition**: Partitions variance into harness, model, task,
  and residual components. Reports percentage of total variance explained.
- **Bootstrap CIs**: Non-parametric bootstrap confidence intervals (default
  1000 resamples, 95% CI) for success rate by harness.
- **Consistency Analysis**: Per harness × model combination, reports mean,
  std, coefficient of variation, min/max success, and bootstrap CI.
- **Warnings**: Automatically warns when sample size is too small (<30)
  or when the mixed-effects model fails to converge.

## Architecture

- **Gateway**: custom HTTP/SSE proxy that intercepts provider calls and captures
  token usage, cost, and latency with full request/response logging.
- **Orchestrator**: builds the eval matrix (harness × model × task × repeats),
  manages budget caps, and handles cell-level resumability.
- **Runner**: Docker-based isolation, one container per eval cell.
- **Adapters**: per-harness integration (Python core + TS shims where needed).
- **Evaluator**: SWE-bench-style (hidden tests) and open-ended (LLM judge) tracks.
- **Reporting**: CLI reports + static HTML + interactive web dashboard.
