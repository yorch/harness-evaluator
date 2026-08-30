# AGENTS.md — heval

## Commands

```bash
# Install
uv sync --extra dev

# Lint (fast, ~1s)
uv run ruff check src/ tests/

# Type check (fast, ~3s)
uv run mypy src/heval/

# Tests (full suite ~10s, 271 tests)
uv run pytest tests/ -q

# All gates at once — must pass before completing any change
uv run ruff check src/ tests/ && uv run mypy src/heval/ && uv run pytest tests/ -q

# Build Docker image (slow, ~5 min — only needed when changing Dockerfile)
docker build -t heval-runner:latest .
```

## Verification

A change is incomplete until all three gates pass: ruff, mypy, pytest.
Run focused tests first when iterating: `uv run pytest tests/<dir>/ -q`.

## Architecture

Python core that orchestrates Node.js coding harnesses running inside Docker
containers, with all provider traffic routed through a custom aiohttp gateway
proxy for token/cost accounting.

- `src/heval/gateway/` — HTTP/SSE proxy, parsers, SQLite store, reconciliation
- `src/heval/orchestrator/` — Matrix builder, budget engine, results store
- `src/heval/runner/` — Docker lifecycle (container per cell, exec-based)
- `src/heval/adapters/` — Per-harness CLI wrappers (claude, codex, opencode, pi, omp)
- `src/heval/evaluator/` — SWE hidden-test + open-ended LLM judge tracks;
  `evaluator/utils.py` holds the shared, symlink-safe `get_workdir_diff`
- `src/heval/dashboard/` — FastAPI dashboard with Jinja2 templates
- `src/heval/stats/` — Mixed-effects model, variance decomposition, bootstrap CIs
- `src/heval/cli.py` — Typer-based CLI entry point
- `tasks/` — Task YAML definitions and repo fixtures (bundled into the wheel
  at `heval/tasks` so an installed heval runs without a repo checkout)
- `Dockerfile` — Image with all 5 harnesses + Bun (node:22-slim base). Harness
  versions are build args (`CLAUDE_CODE_VERSION`, etc.) with pinned defaults.

## Code style

- Line length: 100 chars
- Ruff rules: E, F, W, I, UP, B, SIM, C4
- Strict mypy (no `Any` without justification, all functions typed)
- pytest-asyncio with auto mode
- Do not add or remove comments unless asked

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

- **Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`
- **Scope**: optional, the module or area affected (e.g. `gateway`, `runner`, `docker`)
- **Description**: lowercase, imperative mood, no trailing period
- **Body**: wrap at 100 chars, explain *why* not *what*
- **Breaking change**: add `BREAKING CHANGE:` in the footer or `!` after the type

Examples:
```
feat(gateway): strip trace headers before upstream forwarding
fix(runner): use /workspace/repo as container exec cwd
docs: rewrite AGENTS.md following best practices
ci: bump actions/checkout to v7
```

## Boundaries

- Do not edit `tasks/repos/*/` contents directly — they are task fixtures.
  Change the source and re-init via the runner's `_git_init_fresh`.
- Do not add production dependencies without running `uv add <pkg>` (not
  manual `pyproject.toml` edits).
- The gateway proxy must never forward internal trace headers
  (`x-heval-trace-id`, `x-trace-id`) or the `trace_id` query param upstream.
- The dashboard has no auth — keep it localhost-only by default.
- Task YAMLs are trusted input: `test_command` runs on the host and
  `setup_script` runs in the container. Do not load untrusted task libraries.

## Known traps

- Task repos in `tasks/repos/` are plain directories (no `.git`). The runner
  copies them via `shutil.copytree` and inits a fresh git repo. Do not assume
  `repo_commit` hashes in task YAMLs are valid for these repos.
- Adapters' `get_command()` must use bare binary names (e.g. `"claude"`), not
  `shutil.which()` resolved paths — the binary lives inside the container.
- `_clone_repo` resolves relative paths against the project root
  (`Path(__file__).parents[3]`), not the current working directory.
- Budget reservation is async-safe (single-process `asyncio.Lock`), not
  thread-safe. Do not run the orchestrator across multiple processes.
- The open-ended judge routes through the gateway only when `gateway_url` is
  set. Direct API calls are a fallback for testing only.
- `statsmodels` emits `SingularMatrixWarning` and `ConvergenceWarning` on
  small/degenerate datasets — these are expected and not test failures.

## CI

- `.github/workflows/ci.yml` — ruff + mypy + pytest on every push/PR to main
- `.github/workflows/docker.yml` — builds and verifies the Docker image on
  Dockerfile changes (main only for push, PRs verify build)
- `.github/workflows/astro.yml` — builds and deploys the Astro+Starlight docs
  site to GitHub Pages on changes to `site/`, `docs/`, or the workflow
- `.github/workflows/publish.yml` — builds and publishes the wheel/sdist to
  PyPI (trusted publishing / OIDC) on `v*` tags; docker.yml also pushes a
  version-tagged runner image on `v*` tags
- `.github/workflows/docker-versions.yml` — manually-triggered workflow that
  builds and publishes a per-harness-version runner image (single harness
  build-arg override, tagged `<harness>-<version>`)
