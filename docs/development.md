---
title: Development
description: Contributing to heval, quality gates, code style, testing, project conventions, and CI.
---

# Development

This guide covers everything you need to contribute to heval: setting up the dev environment, running quality gates, understanding code style, writing tests, and following project conventions.

## Setup

```bash
# Install dependencies (including dev tools)
uv sync --extra dev

# Build the Docker image (only needed when changing the Dockerfile)
docker build -t heval-runner:latest .
```

## Quality gates

A change is incomplete until all three gates pass: ruff, mypy, pytest.

```bash
# Lint (fast, ~1s)
uv run ruff check src/ tests/

# Type check (fast, ~3s)
uv run mypy src/heval/

# Tests (full suite ~10s, 271 tests)
uv run pytest tests/ -q

# All gates at once
uv run ruff check src/ tests/ && uv run mypy src/heval/ && uv run pytest tests/ -q
```

### Running focused tests

When iterating on a specific module, run only its tests first:

```bash
uv run pytest tests/gateway/ -q
uv run pytest tests/orchestrator/ -q
uv run pytest tests/adapters/ -q
uv run pytest tests/evaluator/ -q
uv run pytest tests/runner/ -q
uv run pytest tests/reporting/ -q
uv run pytest tests/stats/ -q
uv run pytest tests/dashboard/ -q
```

### Docker integration tests

Docker integration tests require the `heval-runner:latest` image and are skipped if Docker is not available:

```bash
uv run pytest tests/runner/test_docker_integration.py -q
```

## Code style

### Ruff

Line length: 100 characters. Ruff rules: `E`, `F`, `W`, `I`, `UP`, `B`, `SIM`, `C4`.

Configured in `pyproject.toml`:

```toml
[tool.ruff]
src = ["src", "tests"]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "C4"]

[tool.ruff.lint.isort]
known-first-party = ["heval"]
```

### Mypy

Strict mypy: no `Any` without justification, all functions typed.

```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
packages = ["heval"]

[[tool.mypy.overrides]]
module = ["pandas", "statsmodels.*", "numpy.*"]
ignore_missing_imports = true
```

### pytest

pytest-asyncio with auto mode:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### Comments

Do not add or remove comments unless asked. Existing comments are intentional and document design decisions, security considerations, and known traps.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

`feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`

### Scope

Optional. The module or area affected (e.g. `gateway`, `runner`, `docker`, `adapters`, `orchestrator`, `evaluator`, `stats`, `reporting`, `dashboard`).

### Rules

- Description: lowercase, imperative mood, no trailing period
- Body: wrap at 100 chars, explain *why* not *what*
- Breaking change: add `BREAKING CHANGE:` in the footer or `!` after the type

### Examples

```
feat(gateway): strip trace headers before upstream forwarding
fix(runner): use /workspace/repo as container exec cwd
docs: rewrite AGENTS.md following best practices
ci: bump actions/checkout to v7
feat(adapters): add codex adapter with config override support
fix(orchestrator): use real cell count for budget estimation
test(gateway): add SSE boundary splitting tests
```

## Architecture overview

Python core that orchestrates Node.js coding harnesses running inside Docker containers, with all provider traffic routed through a custom aiohttp gateway proxy for token/cost accounting.

| Directory | Responsibility |
|-----------|---------------|
| `src/heval/gateway/` | HTTP/SSE proxy, parsers, SQLite store, reconciliation |
| `src/heval/orchestrator/` | Matrix builder, budget engine, results store |
| `src/heval/runner/` | Docker lifecycle (container per cell, exec-based) |
| `src/heval/adapters/` | Per-harness CLI wrappers (claude, codex, opencode, pi, omp) |
| `src/heval/evaluator/` | SWE hidden-test + open-ended LLM judge tracks |
| `src/heval/dashboard/` | FastAPI dashboard with Jinja2 templates |
| `src/heval/stats/` | Mixed-effects model, variance decomposition, bootstrap CIs |
| `src/heval/cli.py` | Typer-based CLI entry point |
| `tasks/` | Task YAML definitions and repo fixtures |
| `Dockerfile` | Image with all 5 harnesses (node:22-slim base) |

See [Architecture](architecture/) for the full component map and data flow.

## Boundaries

### Do not

- **Edit `tasks/repos/*/` contents directly** — they are task fixtures. Change the source and re-init via the runner's `_git_init_fresh`.
- **Add production dependencies without `uv add <pkg>`** — do not manually edit `pyproject.toml` dependencies.
- **Forward internal trace headers upstream** — the gateway proxy must never forward `x-heval-trace-id`, `x-trace-id`, or the `trace_id` query param to the real provider API.
- **Expose the dashboard externally** — it has no auth, keep it localhost-only.

### Do

- Use bare binary names in `get_command()` (e.g. `"claude"`), not `shutil.which()` resolved paths — the binary lives inside the container.
- Resolve relative `repo_url` paths against the project root (`Path(__file__).parents[3]`), not the current working directory.
- Use `asyncio.Lock` for budget reservation (single-process only — not thread-safe).
- Route the open-ended judge through the gateway when `gateway_url` is set. Direct API calls are a fallback for testing only.

## Known traps

### Task repos have no `.git`

Task repos in `tasks/repos/` are plain directories (no `.git`). The runner copies them via `shutil.copytree` and inits a fresh git repo. Do not assume `repo_commit` hashes in task YAMLs are valid for these repos.

### Budget reservation is not thread-safe

Budget reservation uses a single-process `asyncio.Lock`, not a thread-safe lock. Do not run the orchestrator across multiple processes — budget tracking will break.

### statsmodels warnings

`statsmodels` emits `SingularMatrixWarning` and `ConvergenceWarning` on small/degenerate datasets. These are expected and not test failures.

### Adapter `get_command()` must use bare names

Adapters' `get_command()` must use bare binary names (e.g. `"claude"`), not `shutil.which()` resolved paths. The binary lives inside the Docker container, not on the host. Using `shutil.which()` on the host would fail or resolve to the wrong binary.

### `_clone_repo` path resolution

`_clone_repo` resolves relative paths against the project root (`Path(__file__).resolve().parents[3]`), not the current working directory. This means `repo_url: tasks/repos/swe-bugfix-001` works regardless of where `heval run` is invoked.

## Testing

### Test structure

Tests mirror the source structure:

```
tests/
├── adapters/
│   ├── test_adapters.py      # Registry, adapter listing
│   ├── test_base.py          # BaseAdapter, get_env, gateway URL
│   └── test_codex.py         # Codex-specific tests
├── dashboard/
│   └── test_app.py           # Dashboard endpoints
├── evaluator/
│   ├── test_swe.py           # SWEEvaluator, error classification
│   └── test_open_ended.py    # Judge, rubric, structural checks, calibration
├── gateway/
│   ├── conftest.py           # Shared fixtures (mock proxy, test DB)
│   ├── test_anthropic_parser.py
│   ├── test_openai_parser.py
│   ├── test_proxy.py         # Proxy request handling, SSE, non-streaming
│   ├── test_reconcile.py     # Token reconciliation
│   └── test_security.py      # Header redaction, trace header stripping
├── orchestrator/
│   ├── test_config.py        # Config parsing, matrix building, validation
│   ├── test_engine.py        # Orchestrator, budget, retry, resumability
│   └── test_results_store.py # Results store CRUD
├── reporting/
│   └── test_static_report.py # Report generation, path traversal
├── runner/
│   ├── test_docker.py        # Docker runner (mocked subprocess)
│   └── test_docker_integration.py  # Real Docker (skipped if no Docker)
└── stats/
    └── test_stats.py         # Statistical analysis
```

### Writing tests

- Use `pytest-asyncio` with auto mode — async test functions are automatically detected
- Use `conftest.py` for shared fixtures
- Mock external dependencies (Docker, API calls, filesystem) in unit tests
- Use the gateway `conftest.py` fixtures for proxy tests (mock upstream server, test DB)
- Docker integration tests should be skipped when Docker is not available

### Example test

```python
async def test_budget_cap_skips_cell():
    """Cells should be skipped when budget is exhausted."""
    config = RunConfig(
        name="test",
        harnesses=[...],
        models=[...],
        tasks=["swe-bugfix-001"],
        task_library_path="./tasks",
        repeats=1,
        budget_usd=0.01,  # Very low budget
    )
    store = ResultsStore(":memory:")
    orchestrator = Orchestrator(config, store, run_cell_fn=_dry_run_cell)
    progress = await orchestrator.run()
    assert progress.skipped > 0
```

## CI

### `.github/workflows/ci.yml`

Runs on every push/PR to `main`. Three parallel jobs:

| Job | Tool | Command |
|-----|------|---------|
| Lint | ruff | `uv run ruff check src/ tests/` |
| Type check | mypy | `uv run mypy src/heval/` |
| Tests | pytest | `uv run pytest tests/ -q` |

A quality-gate job depends on all three and must pass for PRs to be mergeable.

### `.github/workflows/docker.yml`

Builds and verifies the Docker image on Dockerfile changes:

- **PRs**: builds the image (no push) and verifies all harnesses are installed
- **Main pushes**: builds, pushes to `ghcr.io`, and verifies

Image tags: `latest`, `sha-<short-hash>`, `<branch>` (main only).

### `.github/workflows/astro.yml`

Builds the Astro + Starlight documentation site from `site/` and deploys it to GitHub Pages on every push to `main` that changes files in `site/`, `docs/`, or the workflow itself. The site consumes Markdown from the root `docs/` directory via a custom Astro content loader.

## Adding a new harness adapter

See [Adapters](adapters/#adding-a-new-adapter) for the step-by-step guide.

## Adding a new task

1. Create a task YAML file in `tasks/` (e.g. `tasks/my-task.yaml`)
2. Create the repo fixture in `tasks/repos/my-task/` (plain directory, no `.git`)
3. Include `src/` and `tests/` subdirectories with the initial (buggy/incomplete) code
4. For SWE tasks: write a `test_patch` with hidden tests
5. For open-ended tasks: set `expected_files` and optionally `test_command`

See [Configuration](configuration/#task-definitions) for the full task spec.

## Adding a new model to pricing

Add an entry to `DEFAULT_PRICING` in `src/heval/gateway/models.py`:

```python
"my-new-model": PricingTable(
    input_per_million=5.0,
    output_per_million=20.0,
    cache_read_per_million=0.50,
    cache_write_per_million=6.25,
),
```

Without this, the model's cost will be $0 and token usage will not count against the budget (with a warning logged).
