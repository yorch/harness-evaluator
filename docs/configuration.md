---
title: Configuration
description: Run YAML config, task definitions, pricing tables, environment variables, and all configurable options.
---

# Configuration

harness-evaluator is configured through YAML files for run configs and task definitions, with pricing tables and environment variables for cost accounting and API access.

## Run configuration

Run configs are YAML files passed to `harness-evaluator run`. See `runs/sample-run.yaml` and `runs/sample-minimal.yaml` for examples.

### Full schema

```yaml
name: "my-run"                    # Required. [A-Za-z0-9._-] only.
description: "Run description"     # Optional. Human-readable.
harnesses:                         # Required. List of harness specs.
  - name: opencode                 #   Harness identifier
    adapter: opencode              #   Adapter module name (registry key)
    observability_tier: full       #   full | partial | minimal
    config:                        #   Harness-specific config (optional)
      mode: agent
models:                            # Required. List of model specs.
  - name: claude-sonnet-4-20250514 #   Model identifier
    provider: anthropic            #   anthropic | openai
    api_key_env: ANTHROPIC_API_KEY #   Env var name for API key
    role: implementation           #   implementation | review (default: implementation)
    config:                        #   Model-specific config (optional)
      max_tokens: 16384
tasks:                             # Required. List of task IDs or ["*"] for all.
  - "*"
task_library_path: "./tasks"       # Optional. Defaults to the bundled library.
repeats: 5                         # Optional. Default: 5.
budget_usd: 100.0                  # Optional. Max total spend in USD. null = no cap.
gateway_host: "host.docker.internal" # Optional. Gateway host from inside Docker.
gateway_port: 8877                 # Optional. Gateway port. Default: 8877.
gateway_db: "harness_evaluator_gateway.db"     # Optional. Gateway SQLite DB path.
results_db: "harness_evaluator_results.db"     # Optional. Results SQLite DB path.
workdir: "./harness_evaluator_workdir"         # Optional. Host workdir for cell repos.
docker_image: "..."                # Optional. Defaults to the version-pinned
                                   #   ghcr.io/yorch/harness-evaluator-runner:<harness-evaluator version>.
parallel_runs: 1                   # Optional. Parallel container runs. Default: 1.
```

### Field reference

#### `name`

Run identifier. Used as the primary key in the results store and in report filenames. Must match `[A-Za-z0-9._-]+`.

#### `harnesses`

List of harness specifications. Each harness is paired with each model to form the eval matrix.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Harness identifier (validated against `[A-Za-z0-9._-]+`) |
| `adapter` | string | Yes | Adapter registry name (e.g. `opencode`, `claude-code`) |
| `observability_tier` | string | No | `full`, `partial`, or `minimal` (default: `partial`) |
| `config` | dict | No | Harness-specific config passed to the adapter |
| `docker_image` | string | No | Per-harness runner image override (see below) |
| `version` | string | No | Image tag on the run-level image's repo (see below) |

##### Choosing a harness version

By default every harness in a run uses the run-level `docker_image`. To evaluate
a specific harness version, set a per-harness image. Precedence is
`docker_image` > `version` > the run-level `docker_image`:

```yaml
docker_image: "ghcr.io/yorch/harness-evaluator-runner:0.1.0"   # run-level default
harnesses:
  # Explicit image (built with a harness build arg — see Docker image config)
  - name: claude-code-2.0
    adapter: claude-code
    docker_image: "ghcr.io/yorch/harness-evaluator-runner:cc-2.0.0"
  # `version` shorthand: uses this as the tag on the run-level image's repo,
  # i.e. ghcr.io/yorch/harness-evaluator-runner:cc-2.1.0
  - name: claude-code-2.1
    adapter: claude-code
    version: "cc-2.1.0"
```

Because a harness entry's `name` is just an identifier and `adapter` is
separate, you can put two versions of the *same* harness in one matrix (as
above) to compare them directly. The resolved image is recorded in each result's
`harness_metadata` for reproducibility. You are responsible for building/pushing
the referenced images (see [Building a specific harness version](#building-a-specific-harness-version)).

#### `models`

List of model specifications. Each model is paired with each harness.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Model identifier (validated against `[A-Za-z0-9._-]+`) |
| `provider` | string | Yes | `anthropic` or `openai` |
| `api_key_env` | string | Yes | Environment variable name for the API key |
| `role` | string | No | `implementation` (default) or `review`. Only affects `multi_phase` tasks — see [Multi-phase evaluation guide](../guides/multi-phase/). |
| `config` | dict | No | Model-specific config (temperature, max_tokens, etc.) |

#### `tasks`

List of task IDs to run, or `["*"]` to run all tasks in the library. Task IDs are resolved against the task library.

#### `task_library_path`

Path to a directory containing task YAML files. All `*.yaml` files in this directory are loaded as the task library. Optional — defaults to the task library bundled inside the installed `harness-evaluator` package (`harness_evaluator/tasks`), so an installed harness-evaluator works without a repo checkout. Local `repo_url` fixtures are resolved relative to this directory.

#### `repeats`

Number of repeats per cell (harness × model × task). Default: 5. Each repeat is an independent run with a fresh container and repo checkout.

#### `budget_usd`

Maximum total spend in USD. When set, the orchestrator uses a reserve-and-reconcile pattern to prevent overspending. Cells are skipped when the remaining budget is insufficient. Set to `null` or omit for no cap.

#### `parallel_runs`

Number of parallel container runs. Default: 1 (sequential). With `parallel_runs > 1`, an `asyncio.Semaphore` limits concurrent executions.

> **Warning**: Budget reservation is async-safe (single-process `asyncio.Lock`), not thread-safe. Do not run the orchestrator across multiple processes.

### Minimal example

```yaml
name: "minimal-first-run"
description: "Minimal first run: one harness, one model, one task"
harnesses:
  - name: opencode
    adapter: opencode
    observability_tier: full
    config:
      mode: agent
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    config:
      max_tokens: 16384
tasks:
  - "swe-bugfix-001"
task_library_path: "./tasks"
repeats: 1
budget_usd: 5.0
```

### Full sweep example

```yaml
name: "broad-first-pass"
description: "Broad first pass: all 5 harnesses, 2 providers, both task tracks"
harnesses:
  - name: opencode
    adapter: opencode
    observability_tier: full
    config:
      mode: agent
  - name: claude-code
    adapter: claude-code
    observability_tier: partial
    config:
      max_turns: 50
  - name: codex
    adapter: codex
    observability_tier: partial
    config: {}
  - name: pi
    adapter: pi
    observability_tier: minimal
    config: {}
  - name: omp
    adapter: omp
    observability_tier: minimal
    config: {}
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    config:
      max_tokens: 16384
  - name: gpt-4o
    provider: openai
    api_key_env: OPENAI_API_KEY
    config:
      max_tokens: 16384
tasks:
  - "*"
task_library_path: "./tasks"
repeats: 5
budget_usd: 100.0
```

### Multi-phase example

A multi-phase run pairs an implementation model with an adversarial reviewer model. See `runs/sample-multi-phase.yaml` and `tasks/multi-phase-bugfix-001.yaml` for complete examples.

```yaml
name: multi-phase-demo
harnesses:
  - name: claude-code
    adapter: claude_code
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    role: implementation
  - name: claude-opus-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    role: review
tasks:
  - multi-phase-bugfix-001
repeats: 1
budget_usd: 10.0
```

The matrix expands to one cell per `implementation × review` model pair. For a walkthrough, see the [Multi-phase evaluation guide](../guides/multi-phase/).

## Task definitions

Tasks are defined as YAML files in the task library directory. Each file can contain multiple tasks under a `tasks:` key.

### Full schema

```yaml
tasks:
- id: swe-bugfix-001               # Required. Unique task identifier.
  name: Fix off-by-one bug          # Required. Human-readable name.
  track: swe                        # Required. swe | open_ended | multi_phase
  difficulty: easy                  # Optional. trivial | easy | medium | hard. Default: medium.
  description: |                    # Optional. Used by the LLM judge for open-ended tasks.
    Detailed description...
  repo_url: tasks/repos/swe-bugfix-001  # Optional. Repo path or URL.
  repo_commit: <commit-hash>       # Optional. Git commit to checkout.
  setup_script: pip install -r requirements.txt  # Optional. Shell script run before harness.
  task_prompt: |-                   # Required. The prompt given to the harness.
    Fix the bug in src/solution.py...    # Ignored when phases is non-empty (multi_phase).
  test_command: python -m pytest tests/  # Optional. Command to run tests.
  test_patch: |                     # Optional. Hidden test patch (SWE track only).
    diff --git a/tests/test_hidden.py...
  expected_files:                   # Optional. Files that should be created/modified.
  - src/solution.py
  timeout_seconds: 300              # Optional. Per-task timeout. Default: 600.
  metadata:                         # Optional. Free-form metadata dict.
    bug_type: off-by-one
    language: python
  phases:                           # Optional. Ordered phases for multi_phase tasks.
  - name: implement                 #   Required. Phase identifier [A-Za-z0-9._-]+.
    model_role: implementation      #   implementation | review. Default: implementation.
    task_prompt: |-                 #   Required. Prompt for this phase.
      Fix the bug in src/solution.py...
    input: none                     #   none | diff | output | review_feedback. Default: none.
    timeout_seconds: 300            #   Optional. Per-phase timeout. Default: 600.
```

### TypeScript tasks

TypeScript tasks use `bun test` as the test runner (Bun is installed in the Docker image). The repo structure uses `.ts` files:

```yaml
tasks:
- id: swe-bugfix-005
  name: Fix sumPositive
  track: swe
  difficulty: easy
  description: Fix the sumPositive function to include zeros and handle empty arrays.
  repo_url: tasks/repos/swe-bugfix-005
  repo_commit: 7a3c9e1f4b2d8a5601c3e7f9d4a8b6c2e0f1d3a5
  setup_script: bun install
  task_prompt: |-
    Fix the `sumPositive` function in src/solution.ts...
  test_command: bun test
  test_patch: |
    diff --git a/tests/test_hidden.test.ts b/tests/test_hidden.test.ts
    new file mode 100644
    ...
  expected_files:
  - src/solution.ts
  timeout_seconds: 300
  metadata:
    bug_type: logic_error
    language: typescript
```

Open-ended TypeScript tasks don't need a `repo_url` or `test_patch` — the harness creates files from scratch:

```yaml
tasks:
- id: open-design-006
  name: Build an HTTP router
  track: open_ended
  difficulty: medium
  task_prompt: 'Design and implement an HTTP router in src/router.ts...'
  test_command: bun test
  expected_files:
  - src/router.ts
  - tests/test_router.test.ts
  timeout_seconds: 600
  metadata:
    design_type: http_router
    language: typescript
```

### Field reference

#### `id`

Unique task identifier. Used in cell IDs, results, and reports. Must be unique within the task library.

#### `track`

Determines which evaluator is used:

| Track | Evaluator | Method |
|-------|-----------|--------|
| `swe` | `SWEEvaluator` | Hidden tests + partial credit |
| `open_ended` | `OpenEndedEvaluator` | LLM judge + rubric + structural checks |
| `multi_phase` | `SWEEvaluator` | Hidden tests after all phases complete (same as `swe`) |

For `multi_phase` tasks, the `phases` field defines an ordered sequence of harness invocations. See the [Multi-phase evaluation guide](../guides/multi-phase/) for a walkthrough.

#### `repo_url`

Repository to clone/copy for the task. Supports:

- **Remote URLs**: `https://...`, `git@...`, `ssh://...` → `git clone`
- **Local git repos**: paths with a `.git` directory → `git clone`
- **Local directories**: paths without `.git` → `shutil.copytree` + `git init`

Relative paths are resolved against the project root, not the current working directory.

#### `setup_script`

Shell script executed inside the container before the harness runs. Written to `/workspace/setup.sh` and executed with `bash /workspace/setup.sh` in the repo directory. Used for installing dependencies, setting up databases, etc.

#### `task_prompt`

The prompt given to the harness. This is the only instruction the harness receives — it does not see the test patch, expected files, or other evaluation metadata.

For `multi_phase` tasks, the top-level `task_prompt` is still required but **ignored** when `phases` is non-empty. Each phase uses its own `task_prompt` from the `PhaseSpec`.

#### `test_command`

Command to run tests. Parsed with `shlex.split` (no `shell=True`) to prevent shell injection. Commands requiring shell features (pipes, redirects) should be wrapped in `bash -c "..."`.

#### `test_patch`

Hidden test patch applied after the harness runs but before evaluation. Applied via `git apply -` from stdin. The harness never sees this patch — it only sees the original repo and the task prompt.

#### `expected_files`

Files that should be created or modified by the harness. Used by the structural checker in the open-ended track to verify the submission includes the expected deliverables.

#### `timeout_seconds`

Per-task timeout in seconds. Applied to both the harness execution and the test command. Default: 600.

#### `metadata`

Free-form dictionary for additional task metadata. Stored with the task but not used by the evaluator. Useful for filtering or grouping tasks in analysis.

#### `phases`

Ordered list of phase definitions for `multi_phase` tasks. Empty (or omitted) for `swe` and `open_ended` tasks. Each phase is a `PhaseSpec`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Phase identifier (validated against `[A-Za-z0-9._-]+`). Must be unique within the task. |
| `model_role` | string | No | `implementation` (default) or `review`. Determines which model runs this phase. |
| `task_prompt` | string | Yes | The prompt given to the harness for this phase. |
| `input` | string | No | What to inject from prior phases: `none` (default), `diff`, `output`, or `review_feedback`. |
| `timeout_seconds` | int | No | Per-phase timeout. Default: 600. |

##### Phase input types

| Input | Injects into the phase prompt |
|-------|-------------------------------|
| `none` | Nothing — the phase runs standalone. |
| `diff` | Git diff from the prior implementation phase (captured before commit). |
| `output` | Stdout + stderr from the prior phase. |
| `review_feedback` | Stdout + stderr from a prior `review` phase. |

##### Model roles

| Role | Used by | Description |
|------|---------|-------------|
| `implementation` | All phases by default | The primary coding model. Assigned via `models[].role: implementation` in the run config. |
| `review` | Phases with `model_role: review` | The adversarial reviewer model. Assigned via `models[].role: review` in the run config. |

##### Validation rules

- `multi_phase` tasks must define at least one phase.
- At least one phase must have `model_role: implementation`.
- Phase names must be unique within a task.
- If any phase has `model_role: review`, the run config must include at least one model with `role: review` and one with `role: implementation`.

### SWE task example

```yaml
tasks:
- id: swe-bugfix-001
  name: Fix off-by-one in list pagination function
  track: swe
  difficulty: easy
  description: |
    The `get_page` function in src/solution.py has an off-by-one bug.
    It calculates the end index as `page_number * page_size - 1` instead
    of `page_number * page_size`, causing the last item of each full page
    to be dropped.
  repo_url: tasks/repos/swe-bugfix-001
  setup_script: pip install -r requirements.txt
  task_prompt: |-
    Fix the off-by-one bug in the `get_page` function in src/solution.py.
    The correct end index should be `page_number * page_size`.
    Run tests with: python -m pytest tests/
  test_command: python -m pytest tests/
  test_patch: |
    diff --git a/tests/test_hidden.py b/tests/test_hidden.py
    new file mode 100644
    --- /dev/null
    +++ b/tests/test_hidden.py
    @@ -0,0 +1,34 @@
    +"""Hidden tests for pagination — verify the off-by-one fix."""
    +from src.solution import get_page
    +def test_full_first_page():
    +    items = list(range(1, 21))
    +    assert get_page(items, 1, 10) == list(range(1, 11))
  expected_files:
  - src/solution.py
  timeout_seconds: 300
  metadata:
    bug_type: off-by-one
    language: python
    test_count: 10
```

### Open-ended task example

```yaml
tasks:
- id: open-design-001
  name: Design a token bucket rate limiter
  track: open_ended
  difficulty: medium
  description: |
    Design and implement a token bucket rate limiter with configurable
    rate and burst capacity. Include comprehensive tests.
  task_prompt: |-
    Design and implement a token bucket rate limiter in src/rate_limiter.py.
    Requirements:
    - Configurable rate (tokens per second) and burst capacity
    - `allow(n=1)` method that returns True if n tokens are available
    - Tokens refill at the configured rate, up to the burst capacity
    - Thread-safe implementation
    Add comprehensive tests in tests/test_rate_limiter.py.
  test_command: python -m pytest tests/
  expected_files:
  - src/rate_limiter.py
  - tests/test_rate_limiter.py
  timeout_seconds: 600
  metadata:
    design_type: rate_limiter
    language: python
```

## Pricing tables

Cost is calculated from per-token pricing tables in `src/harness_evaluator/gateway/models.py`. Prices are in USD per 1 million tokens.

### Default pricing

**Anthropic current generation:**

| Model | Input | Output | Cache read | Cache write |
|-------|-------|--------|------------|-------------|
| `claude-fable-5` | $10.00 | $50.00 | $1.00 | $12.50 |
| `claude-opus-5` | $5.00 | $25.00 | $0.50 | $6.25 |
| `claude-sonnet-5` | $2.00 | $10.00 | $0.20 | $2.50 |
| `claude-haiku-4-5-20251001` | $1.00 | $5.00 | $0.10 | $1.25 |

**Anthropic previous generation (still available):**

| Model | Input | Output | Cache read | Cache write |
|-------|-------|--------|------------|-------------|
| `claude-opus-4-5-20251101` | $5.00 | $25.00 | $0.50 | $6.25 |
| `claude-opus-4-8` | $5.00 | $25.00 | $0.50 | $6.25 |
| `claude-sonnet-4-6` | $3.00 | $15.00 | $0.30 | $3.75 |
| `claude-sonnet-4-5` | $3.00 | $15.00 | $0.30 | $3.75 |
| `claude-sonnet-4-20250514` | $3.00 | $15.00 | $0.30 | $3.75 |
| `claude-opus-4-20250514` | $15.00 | $75.00 | $1.50 | $18.75 |
| `claude-haiku-3-5-20241022` | $0.80 | $4.00 | $0.08 | $1.00 |

**OpenAI current generation (GPT-5.6 family):**

| Model | Input | Output | Cache read | Cache write |
|-------|-------|--------|------------|-------------|
| `gpt-5.6-sol` / `gpt-5.6` | $4.00 | $20.00 | $0.40 | $5.00 |
| `gpt-5.6-terra` | $2.00 | $12.00 | $0.20 | $2.50 |
| `gpt-5.6-luna` | $0.20 | $1.20 | $0.02 | $0.25 |

**OpenAI previous generation (still available):**

| Model | Input | Output | Cache read |
|-------|-------|--------|------------|
| `gpt-5` | $1.25 | $10.00 | — |
| `gpt-5-mini` | $0.25 | $2.00 | — |
| `gpt-5-nano` | $0.05 | $0.30 | — |
| `o3` | $2.00 | $8.00 | $0.50 |
| `o4-mini` | $1.10 | $4.00 | $0.55 |

**OpenAI legacy (for backward compatibility):**

| Model | Input | Output | Cache read |
|-------|-------|--------|------------|
| `gpt-4o` | $2.50 | $10.00 | $1.25 |
| `gpt-4o-mini` | $0.15 | $0.60 | $0.075 |

### Unknown models

When a model is not in the pricing table, `get_pricing_strict()` logs a warning and returns a zero-cost `PricingTable`. This means token usage will not count against the budget — a silent budget bypass. The warning makes this visible:

```
WARNING: No pricing found for model 'my-custom-model'; cost will be $0
and token usage will NOT count against the budget.
Add the model to DEFAULT_PRICING to fix this.
```

### Adding a new model

Add an entry to `DEFAULT_PRICING` in `src/harness_evaluator/gateway/models.py`:

```python
DEFAULT_PRICING: dict[str, PricingTable] = {
    # ... existing entries ...
    "my-new-model": PricingTable(
        input_per_million=5.0,
        output_per_million=20.0,
        cache_read_per_million=0.50,
        cache_write_per_million=6.25,
    ),
}
```

## Environment variables

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (for Anthropic models and judge calibration) |
| `OPENAI_API_KEY` | OpenAI API key (for OpenAI models) |

### Set by adapters (inside containers)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_BASE_URL` | Gateway proxy URL for Anthropic (with `?trace_id=`) |
| `OPENAI_BASE_URL` | Gateway proxy URL for OpenAI (with `/v1` and `?trace_id=`) |
| `ANTHROPIC_API_KEY` | Passed through from host |
| `OPENAI_API_KEY` | Passed through from host |
| `HARNESS_EVALUATOR_TRACE_ID` | Cell trace ID for cost attribution |

### Allowlisted (passed from host to container)

| Variable | Description |
|----------|-------------|
| `PATH` | Executable search path |
| `HOME` | Home directory |
| `USER` | Username |
| `SHELL` | Default shell |
| `LANG` | Locale |
| `LC_ALL` | Locale override |
| `TERM` | Terminal type |
| `TMPDIR` | Temporary directory |

## Authentication modes

By default, harness-evaluator authenticates to provider APIs using API keys
(`auth_mode: api_key`). For harnesses that support subscription-based access
(Claude Code OAuth, Codex with a ChatGPT subscription), you can switch to an
OAuth/subscription auth mode so the harness uses your existing subscription
instead of pay-per-token API billing.

### The three auth modes

| Mode | Value | Description |
|------|-------|-------------|
| API key | `api_key` | Default. Uses the env var named in `api_key_env` (e.g. `ANTHROPIC_API_KEY`). |
| Claude Code OAuth | `claude_oauth` | Uses a Claude Code OAuth token or credential file. No API key is sent. |
| Codex ChatGPT | `codex_chatgpt` | Uses a Codex/ChatGPT subscription credential file. Routes through the ChatGPT backend. |

### Model spec fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `auth_mode` | string | No | `api_key` (default), `claude_oauth`, or `codex_chatgpt` |
| `credentials_path` | string | No | Path to an OAuth credential file on the host (for subscription auth) |
| `cost_mode` | string | No | `platform` (default, pay-per-token) or `subscription` (zero-dollar token-only accounting) |

### `credentials_path`

For `claude_oauth` and `codex_chatgpt` modes, `credentials_path` points to the
OAuth credential file on the host. The Docker runner copies the credential
file's parent directory to a temp directory and mounts it writable into the
container so the harness can refresh expired access tokens. The original
credential files on the host are never modified or mounted directly.

The appropriate config env var is set so the harness finds its tokens:

- `claude_oauth` → mounts to `/workspace/.claude`, sets `CLAUDE_CONFIG_DIR`
- `codex_chatgpt` → mounts to `/workspace/.codex`, sets `CODEX_HOME`

If the file does not exist, the runner logs a warning and skips the mount (the
harness will likely fail to authenticate).

### `cost_mode`

- `platform` (default): Standard pay-per-token cost accounting. Token usage is
  priced against the `DEFAULT_PRICING` table and counts against `budget_usd`.
- `subscription`: The harness runs on a flat-rate subscription. Token usage is
  still captured for analysis, but cost is recorded as $0 and does not count
  against `budget_usd`. Use this when running on a ChatGPT or Claude Pro
  subscription where you are not billed per token.

### Security considerations

OAuth credential files contain **refresh tokens** that grant ongoing access to
your account. Treat them with the same care as API keys:

- Store credential files with restrictive permissions (`chmod 600`).
- Never commit credential files to a repository.
- The Docker runner copies credentials to a temp directory and mounts that
  (not the original) so the harness can refresh tokens without touching your
  real credential files. The mount is writable so token refresh works.
- A writable mount means the harness process can read the refresh token.
  Task YAMLs are trusted input (see [Task trust model](#task-trust-model)),
  but be aware that a malicious task could exfiltrate OAuth tokens via the
  network. This is the same risk as API keys — the container has network
  access to the gateway.
- Credential mount points (`.claude`, `.codex`) are excluded from the git
  commit diff (including nested paths) as defense in depth, so tokens never
  appear in evaluation diffs.

### Example: API key (default)

```yaml
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
```

### Example: Claude Code OAuth

```yaml
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    auth_mode: claude_oauth
    credentials_path: "~/.claude/.credentials.json"
    cost_mode: subscription
```

With `claude_oauth`, the adapter sets `ANTHROPIC_BASE_URL` to the gateway
proxy but does **not** set `ANTHROPIC_API_KEY`. If the `CLAUDE_CODE_OAUTH_TOKEN`
environment variable is present on the host, it is passed through to the
container.

### Example: Codex ChatGPT subscription

```yaml
models:
  - name: gpt-4o
    provider: openai
    api_key_env: OPENAI_API_KEY
    auth_mode: codex_chatgpt
    credentials_path: "~/.codex/auth.json"
    cost_mode: subscription
```

With `codex_chatgpt`, the Codex adapter passes `chatgpt_base_url` (with a
`/codex` path) via the `-c` config flag instead of `openai_base_url`. The
gateway proxy routes `/codex/responses` and `/codex/` paths to the ChatGPT
backend (`https://chatgpt.com/backend-api/codex`). `OPENAI_API_KEY` and
`OPENAI_BASE_URL` are not set.

## Identifier validation

All identifiers (run names, harness names, model names) are validated against `[A-Za-z0-9._-]+` to prevent path traversal and shell injection. Invalid characters cause a `ValueError` at config load time.

## Docker image configuration

The runner image contains all five harnesses and their dependencies. You can either pull the pre-built image from GHCR or build it locally. See [Docker Runner](docker-runner/) for details on the image contents.

### Pull the pre-built image (recommended)

```bash
docker pull ghcr.io/yorch/harness-evaluator-runner:latest
```

Then reference it in your run config:

```yaml
docker_image: "ghcr.io/yorch/harness-evaluator-runner:latest"
```

Available tags: `latest`, `sha-<short-hash>` (pinned to a commit), semver tags
like `1.2.3` and `1.2` (published from `v*` release tags), and `main`.

The default `docker_image` is version-pinned to the installed harness-evaluator version
(`ghcr.io/yorch/harness-evaluator-runner:<harness-evaluator version>`) so a given harness-evaluator release pairs
with a matching runner image for reproducibility.

### Build locally

```bash
docker build -t harness-evaluator-runner:latest .
```

Then set `docker_image: "harness-evaluator-runner:latest"` (or any custom name) in the run config.

### Building a specific harness version

Harness versions are build args, so you can build an image that pins a specific
harness release to compare versions:

```bash
docker build --build-arg CLAUDE_CODE_VERSION=2.0.0 -t harness-evaluator-runner:cc-2.0.0 .
```

Available build args (defaulting to the verified pinned set): `CLAUDE_CODE_VERSION`,
`CODEX_VERSION`, `OPENCODE_VERSION`, `PI_VERSION`, `OMP_VERSION`, `BUN_VERSION`.
The installed versions are recorded as `io.harness-evaluator.*` OCI image labels, and the
image name is stored in each run's metadata, so results trace to exact versions.
Reference the built image via `docker_image:` in the run config.

### Publishing a per-harness-version image

The `docker-versions.yml` workflow (manual trigger) builds and publishes a
runner image with a single harness version override, tagged as
`<harness>-<version>` (e.g. `claude-code-2.0.0`). Trigger it from the GitHub
Actions UI with the harness build-arg name and the version to pin. The
resulting image is pushed to GHCR and can be referenced directly:

```yaml
harnesses:
  - name: claude-code-2.0
    adapter: claude-code
    docker_image: "ghcr.io/yorch/harness-evaluator-runner:claude-code-2.0.0"
```

## Task trust model

Task YAMLs — including `test_command`, `setup_script`, and `repo_url` — are
treated as **trusted input**. The SWE evaluator and the open-ended structural
checker run a task's `test_command` on the **host** (not inside the container),
and `setup_script` runs inside the container. Do not load task libraries from
untrusted sources. harness-evaluator still validates task `id` and `repo_commit` against a
safe charset and skips symlinked untracked files during diff extraction as
defense in depth, but a hostile task definition can execute arbitrary commands.

## Task library structure

```
tasks/
├── swe-bugfix-001.yaml          # Task definitions (20 total)
├── swe-bugfix-002.yaml
├── swe-bugfix-003.yaml
├── swe-bugfix-004.yaml
├── swe-bugfix-005.yaml
├── swe-feature-001.yaml
├── swe-feature-002.yaml
├── swe-feature-003.yaml
├── swe-perf-001.yaml
├── swe-perf-002.yaml
├── swe-refactor-001.yaml
├── swe-refactor-002.yaml
├── open-design-001.yaml
├── open-design-002.yaml
├── open-design-003.yaml
├── open-design-004.yaml
├── open-design-005.yaml
├── open-design-006.yaml
├── open-design-007.yaml
├── open-design-008.yaml
└── repos/                       # Task repo fixtures (SWE only)
    ├── swe-bugfix-001/
    │   ├── src/
    │   │   ├── __init__.py
    │   │   └── solution.py
    │   └── tests/
    │       ├── __init__.py
    │       └── test_solution.py
    ├── swe-bugfix-002/
    └── ...
```

### Task mix overview

The library contains 20 tasks across two tracks and two languages:

| Track | Count | Python | TypeScript | Difficulties |
|-------|-------|--------|------------|--------------|
| SWE | 12 | 9 | 3 | easy, medium, hard |
| Open-ended | 8 | 5 | 3 | easy, medium, hard |

**SWE tasks** (bug fixes, features, refactors, performance):

| ID | Type | Difficulty | Language | Description |
|----|------|-----------|----------|-------------|
| swe-bugfix-001 | bugfix | easy | Python | Off-by-one in list pagination |
| swe-bugfix-002 | bugfix | medium | Python | CSV parser quoted fields |
| swe-bugfix-003 | bugfix | easy | Python | `deep_get` KeyError on missing key |
| swe-bugfix-004 | bugfix | hard | Python | Async rate limiter race condition |
| swe-bugfix-005 | bugfix | easy | TypeScript | `sumPositive` excludes zeros |
| swe-feature-001 | feature | medium | Python | LRU eviction for cache |
| swe-feature-002 | feature | medium | Python | HTTP client retry with backoff |
| swe-feature-003 | feature | medium | TypeScript | Debounce implementation |
| swe-refactor-001 | refactor | easy | Python | Extract duplicated validation |
| swe-refactor-002 | refactor | easy | Python | Extract repeated type checking |
| swe-perf-001 | performance | hard | Python | O(n²) to O(n) duplicate finding |
| swe-perf-002 | performance | medium | TypeScript | O(n²) CSV builder to `join` |

**Open-ended tasks** (design from scratch):

| ID | Difficulty | Language | Design type |
|----|-----------|----------|-------------|
| open-design-001 | medium | Python | Token bucket rate limiter |
| open-design-002 | hard | Python | Multi-source config loader |
| open-design-003 | medium | Python | Priority queue (binary heap) |
| open-design-004 | easy | Python | Circular buffer |
| open-design-005 | hard | Python | Trie-based autocomplete |
| open-design-006 | medium | TypeScript | HTTP router with middleware |
| open-design-007 | medium | TypeScript | Pub/sub event emitter |
| open-design-008 | hard | TypeScript | Finite state machine with guards |

A curated run config that uses all 20 tasks is at `runs/task-mix.yaml`.

> **Note**: Do not edit `tasks/repos/*/` contents directly — they are task fixtures. Change the source and re-init via the runner's `_git_init_fresh`.
