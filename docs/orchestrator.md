---
title: Orchestrator
description: Eval matrix building, budget caps with atomic reservation, retry logic, and cell-level resumability.
---

# Orchestrator

The orchestrator (`src/harness_evaluator/orchestrator/`) is the central execution engine. It takes a run configuration, expands it into a full eval matrix, executes each cell with budget tracking and retry logic, and stores results for reporting and analysis.

## Components

| File | Description |
|------|-------------|
| `config.py` | `RunConfig`, `RunCell`, `TaskSpec`, `HarnessSpec`, `ModelSpec` models |
| `engine.py` | `Orchestrator` class — matrix execution, budget, retry, progress |
| `results_store.py` | `ResultsStore` — SQLite storage for results, state, metadata |

## Matrix building

The eval matrix is the Cartesian product of harnesses × models × tasks × repeats. For `multi_phase` tasks, the matrix additionally expands across implementation and review model pairs.

```
RunConfig.build_matrix()
  │
  ├── expand_tasks()
  │   Load task YAMLs from task_library_path
  │   If tasks=["*"], use all tasks in the library
  │   Otherwise, resolve specific task IDs (validates they exist)
  │
  ├── Partition models by role:
  │   impl_models   = [m for m in models if m.role == implementation]
  │   review_models = [m for m in models if m.role == review]
  │
  └── For each harness × task:
      │
      ├── multi_phase task WITH a review phase:
      │   For each impl_model × review_model × repeat:
      │     Create RunCell(model=impl_model, review_model=review_model)
      │     cell_id = "{harness}__{impl}__{task}__r{repeat}__rev-{review}"
      │
      ├── multi_phase task WITHOUT a review phase:
      │   For each impl_model × repeat:
      │     Create RunCell(model=impl_model, review_model=None)
      │     cell_id = "{harness}__{model}__{task}__r{repeat}"
      │
      └── swe / open_ended task:
          For each model × repeat:
            Create RunCell(model=model, review_model=None)
            cell_id = "{harness}__{model}__{task}__r{repeat}"
```

For example, with 5 harnesses, 2 models, 20 tasks, and 5 repeats, the matrix has **1000 cells**. A multi-phase task with 2 implementation models and 1 review model produces 2 cells per harness per repeat.

### Multi-phase validation

`build_matrix()` raises `ValueError` if:

- A `multi_phase` task has a `review` phase but no models with `role: implementation`.
- A `multi_phase` task has a `review` phase but no models with `role: review`.

### Cell ID format

Each cell has a unique ID:

- **Single-phase**: `{harness.name}__{model.name}__{task.id}__r{repeat}`
- **Multi-phase with review**: `{harness.name}__{model.name}__{task.id}__r{repeat}__rev-{review_model.name}`

Example: `opencode__claude-sonnet-5__swe-bugfix-001__r0`

This ID is used as the `trace_id` for gateway proxy attribution, the Docker container name (sanitized), and the primary key in the results store. For multi-phase tasks, per-phase trace IDs are `{cell_id}__phase-{phase.name}`.

## Execution model

```python
async def run(self) -> OrchestratorProgress:
    cells = self.config.build_matrix()
    # Filter out already-completed cells (resumability)
    completed = self.store.get_completed_cells(self.config.name)
    pending_cells = [c for c in cells if c.cell_id not in completed]

    if self.config.parallel_runs <= 1:
        # Sequential execution
        for cell in pending_cells:
            await self._run_cell_with_budget_check(cell)
    else:
        # Parallel execution with semaphore
        sem = asyncio.Semaphore(self.config.parallel_runs)
        tasks = [self._run_cell_with_budget_and_sem(sem, c) for c in pending_cells]
        await asyncio.gather(*tasks)
```

### Parallel execution

`parallel_runs` controls concurrency. With `parallel_runs=1` (default), cells run sequentially. With `parallel_runs>1`, an `asyncio.Semaphore` limits concurrent cell executions.

> **Warning**: Budget reservation uses a single-process `asyncio.Lock`, not a thread-safe lock. Do not run the orchestrator across multiple processes — budget tracking will break.

## Budget management

The orchestrator uses a **reserve-and-reconcile** pattern for budget enforcement:

```
1. RESERVE (under asyncio.Lock):
   │  Estimate cell cost = budget_usd / total_cells
   │  Skip iff NOT budget-exempt AND (remaining_budget <= 0 OR remaining_budget < estimate)
   │  Otherwise: remaining_budget -= estimate, record reservation
   │
2. EXECUTE (outside lock):
   │  Run the cell via run_cell_fn (Docker runner)
   │  This may take minutes — the lock is not held during execution
   │
3. RECONCILE (under asyncio.Lock):
   │  actual_cost = result["total_cost"]
   │  If actual < reserved → refund difference to remaining_budget
   │  If actual > reserved → deduct shortfall
   │  Save result to store (atomic with reconciliation)
   │  Clear reservation
```

### Cost estimation

A budget-exempt cell (see [Budget exemption](#budget-exemption) below) estimates
`0.0` regardless of `cell.budget` — exemption pre-empts an explicit per-cell
budget. Otherwise, if `cell.budget` is set, that value is used as the
estimate. Failing that, the estimate is `budget_usd / total_cells` (using the
real cell count from `build_matrix()`, not `len(config.tasks)` which is wrong
when `tasks=["*"]`).

### Budget exhaustion

A non-exempt cell is skipped when `remaining_budget <= 0` (no budget left
at all, including exactly `$0.00`) **or** `remaining_budget < estimate`
(there is some budget left, but not enough headroom for this cell's own
estimate) — both halves matter independently: without the second, a cell
whose estimate exceeds a still-positive remaining balance would be wrongly
admitted, overspending the cap. The skip reason persisted to
`run_state.error` includes the dollar amounts for debugging:

```
Budget cap reached ($0.0123 remaining < $0.0500 estimated)
```

The orchestrator logs a warning. After each cell completes, a post-update check warns if the total spend has exceeded the budget (can happen when a cell costs more than its reservation).

### Budget exemption

A cell is budget-exempt — its reservation is always `$0.00` and it can
never be skipped for lack of budget — only when **all three** hold:

1. `cell.model.cost_mode` is `subscription` (the implementation model runs
   under a flat-rate subscription, not metered API billing).
2. `cell.review_model` is absent, or its `cost_mode` is also
   `subscription` (a multi-phase review phase's spend is folded into the
   same trace's total, so a platform-billed reviewer makes the cell
   non-exempt even if the implementation model is subscription).
3. `cell.task.track` is not `open_ended` (the open-ended judge's LLM
   calls are real, platform-billed spend with no subscription coverage).

This is deliberately all-or-nothing at the cell level: a cell that mixes
subscription and platform spend (e.g. a subscription implementation model
with a platform review model) is charged **in full**, not split
proportionally — a cost cap that over-charges is safe, one that
under-charges leaks real money. Per-trace attribution (crediting only the
subscription-covered portion) is a deferred follow-up.

`OrchestratorProgress.total_cost` (the CLI's "Total cost (informational,
includes budget-exempt cells)" line, and the TUI footer's `Cost: … (info)`
figure) always remains the **true total** across every cell, exempt or
not — it is never reduced by exemption. `ResultsStore.get_billable_cost`
is the separate, budget-relevant figure: it sums `total_cost` for every
row whose persisted `cost_mode` is not `'subscription'` (a `NULL`
`cost_mode` is treated as billable — the conservative direction; in
practice this is unreachable via the bundled migration, which backfills
every pre-migration row to `'platform'`, but a future direct row write
that omits the column would still land as billable rather than silently
exempt). The CLI's summary prints both:
`Total cost (informational, …): $X` and, when `budget_usd` is set,
`Billable cost: $Y / $Z budget cap`.

**Overspend debt persists across resume.** `remaining_budget` can go
negative whenever a cell's actual cost exceeds its reservation (most
visibly under `parallel_runs > 1`, where several cells hold reservations
against the same balance at once, but it happens at `parallel_runs: 1`
too — the admission gate only guarantees `remaining >= estimate` before
a cell starts, not that its real cost stays within that estimate) and
that negative balance is not re-floored to `0` or to the full `budget_usd` on
resume — a run that resumes computes `remaining_budget = budget_usd -
get_billable_cost(run_name)` from the real historical spend, so a run
that blew its cap stays blocked on the next resume unless `budget_usd` is
raised. Migrated legacy rows default to `cost_mode = 'platform'`, so
resuming a long subscription run after an upgrade will find historic
subscription spend newly counted against the cap on first resume — safe
(over-charge, never under-charge) but worth knowing.

## Retry logic

Transient failures (`RetryableError`) are retried with exponential backoff:

| Attempt | Delay |
|---------|-------|
| 1 (initial) | — |
| 2 (retry 1) | 2s |
| 3 (retry 2) | 4s |
| 4 (retry 3) | 8s |

After `MAX_RETRIES` (3) attempts, the cell is recorded as `retryable_kill` with `error_class="retry_exhausted"`.

### What triggers a retry

The Docker runner raises `RetryableError` for:
- Container timeouts (`subprocess.TimeoutExpired`)
- Harness command timeouts

Non-retryable exceptions (any `Exception` that isn't `RetryableError`) are recorded as `non_retryable_kill` immediately — no retry.

### Reservation release on failure

When a cell fails (exhausted retries or non-retryable error), its budget reservation is released back to the remaining budget so subsequent cells can use the funds.

## Resumability

Resumability is **cell-level only**:

1. Before execution, the orchestrator queries `run_state` for cells with status `"completed"`
2. Those cells are filtered out of the pending list
3. On re-run, only incomplete cells are executed

```
harness-evaluator run runs/sample-run.yaml     # Runs 1000 cells, crashes after 500
harness-evaluator run runs/sample-run.yaml     # Skips 500 completed, runs remaining 500
```

> **Note**: There is no mid-flight agent process resumption. Incomplete cells are re-run from scratch — the workdir is cleaned, the gateway calls for that trace_id are deleted, and the container starts fresh.

### Workdir cleanup on re-run

The Docker runner deletes the cell's workdir (`shutil.rmtree`) before starting, ensuring no stale git state, dirty working trees, or prior harness output interferes. It also deletes prior gateway calls for the cell's `trace_id` to prevent double-counting token usage.

## Progress tracking

`OrchestratorProgress` tracks:

| Field | Description |
|-------|-------------|
| `total_cells` | Total cells in the matrix |
| `completed` | Cells that passed (exit_class=pass) |
| `failed` | Cells that failed (exit_class=fail or kills) |
| `skipped` | Cells skipped (already completed or budget cap) |
| `running` | Currently executing cells |
| `total_cost` | Cumulative spend across all cells |
| `errors` | List of error messages (first 5 shown by CLI) |

Progress counters are mutated under a `_progress_lock` (`asyncio.Lock`) to prevent lost updates when running in parallel.

## Results store schema

### `run_results` table

Primary key is the composite `(run_name, cell_id)`, not `cell_id` alone —
two runs can share a `cell_id` (e.g. the same harness/model/task/repeat
run twice under different `name:`s) without overwriting each other. See
[Composite keys and the in-place migration](#composite-keys-and-the-in-place-migration)
below.

| Column | Type | Description |
|--------|------|-------------|
| `cell_id` | TEXT | Cell identifier (part of the composite PK, see above) |
| `run_name` | TEXT | Run name from config (part of the composite PK) |
| `harness` | TEXT | Harness name |
| `model` | TEXT | Model name |
| `task_id` | TEXT | Task ID |
| `track` | TEXT | `swe`, `open_ended`, or `multi_phase` |
| `repeat` | INTEGER | Repeat index (0-based) |
| `exit_class` | TEXT | `pass`, `fail`, `retryable_kill`, `non_retryable_kill` |
| `success` | REAL | 0.0–1.0 (partial credit) |
| `error_class` | TEXT | `success`, `partial`, `overfit`, `timeout`, etc. |
| `error_message` | TEXT | Error details |
| `input_tokens` | INTEGER | Total input tokens |
| `output_tokens` | INTEGER | Total output tokens |
| `cache_read_tokens` | INTEGER | Cache read tokens |
| `cache_write_tokens` | INTEGER | Cache write tokens |
| `reasoning_tokens` | INTEGER | Reasoning tokens |
| `total_cost` | REAL | Total cost in USD |
| `latency_ms` | REAL | Wall-clock latency |
| `time_to_first_attempt_ms` | REAL | Time to first solution attempt |
| `num_api_calls` | INTEGER | Number of provider API calls |
| `num_tool_calls` | INTEGER | Number of tool calls |
| `diff` | TEXT | Git diff of changes |
| `test_output` | TEXT | Test command output |
| `harness_metadata` | TEXT | JSON metadata (harness, model, observability tier) |
| `harness_stdout` | TEXT | Sanitized harness stdout (last 50KB; secrets redacted) |
| `harness_stderr` | TEXT | Sanitized harness stderr (last 50KB; secrets redacted) |
| `timestamp` | TEXT | ISO timestamp |
| `retry_count` | INTEGER | Number of retries (0 = first attempt) |
| `cost_mode` | TEXT | `platform` (billable) or `subscription` (budget-exempt); see [Budget exemption](#budget-exemption) |

### `run_state` table

Tracks cell execution state for resumability and live dashboard progress.
Primary key is also the composite `(run_name, cell_id)`.

| Column | Type | Description |
|--------|------|-------------|
| `cell_id` | TEXT | Cell identifier (part of the composite PK) |
| `run_name` | TEXT | Run name (part of the composite PK) |
| `status` | TEXT | `pending`, `running`, `completed`, `failed`, `skipped` |
| `started_at` | TEXT | ISO timestamp |
| `completed_at` | TEXT | ISO timestamp |
| `error` | TEXT | Error message if failed |

### `run_metadata` table

Stores the full run config for reproducibility:

| Column | Type | Description |
|--------|------|-------------|
| `run_name` | TEXT PK | Run name |
| `config_json` | TEXT | Full `RunConfig` as JSON |
| `harness_evaluator_version` | TEXT | harness-evaluator package version |
| `docker_image` | TEXT | Docker image used |
| `created_at` | TEXT | ISO timestamp |

### `phase-results` table

Stores per-phase results for `multi_phase` cells. One row per phase per cell.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `cell_id` | TEXT | Cell ID (part of the composite FK to `run_results(run_name, cell_id)`) |
| `run_name` | TEXT | Run name (part of the composite FK) |
| `phase_name` | TEXT | Phase name (e.g. `implement`, `review`, `revise`) |
| `trace_id` | TEXT | Per-phase gateway trace ID (`{cell_id}__phase-{name}`) |
| `model` | TEXT | Model name used in this phase |
| `model_role` | TEXT | `implementation` or `review` |
| `exit_code` | INTEGER | Phase exit code |
| `duration_ms` | REAL | Phase duration in milliseconds |
| `timed_out` | INTEGER | 1 if the phase timed out, 0 otherwise |
| `input_tokens` | INTEGER | Input tokens consumed |
| `output_tokens` | INTEGER | Output tokens consumed |
| `total_cost` | REAL | Phase cost in USD |
| `num_api_calls` | INTEGER | Number of API calls in this phase |
| `error` | TEXT | Error message (if any) |
| `stdout` | TEXT | Sanitized phase stdout (last 50KB; secrets redacted) |
| `stderr` | TEXT | Sanitized phase stderr (last 50KB; secrets redacted) |
| `timestamp` | TEXT | ISO timestamp |

Query per-phase costs with:

```sql
SELECT phase_name, model, total_cost, input_tokens, output_tokens
FROM phase_results
WHERE cell_id = ?
ORDER BY id ASC;
```

The `harness_metadata` JSON in `run_results` also includes a `phases` list (with trace IDs, models, durations, and exit codes) and a `review_model` field for quick access without joining.

### Harness output capture and secret redaction

When a harness runs, its stdout and stderr are captured and stored in
`run_results.harness_stdout` / `run_results.harness_stderr` (and
`phase_results.stdout` / `phase_results.stderr` for multi-phase tasks).
This is essential for debugging cells where the harness produced no changes
(e.g. API key invalid, rate limited, crash) — the output explains *why*.

Before persistence, the output is sanitized by `src/harness_evaluator/runner/redaction.py`:

- **Secret redaction**: API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`),
  OAuth tokens (`CLAUDE_CODE_OAUTH_TOKEN`), bearer tokens, and `sk-` prefixed
  keys are replaced with `[REDACTED]`. This prevents secret leakage to the
  database, dashboard, and CSV/JSON exports.
- **Truncation**: Output is capped to the last 50KB per stream (error
  messages and stack traces appear at the end). A truncation notice is
  prepended when output is cut.

Existing databases are migrated in place via `ALTER TABLE` — no data loss.

### Composite keys and the in-place migration

`run_results`, `run_state`, `reconciliation_results`, and `phase_results`
key on the composite `(run_name, cell_id)` rather than a bare `cell_id`.
Since `cell_id` is derived from `harness`/`model`/`task`/`repeat` alone
(not `run_name`), two different runs can produce the same `cell_id` — for
example, re-running an identical matrix under a new `name:` to compare
against a prior run. Before the composite key, the second run's first
cell would silently overwrite the first run's row with the same
`cell_id`; every store query and the dashboard are now run-scoped so this
can no longer happen.

Opening an existing database rebuilds these tables to the composite key
in place, inside one transaction with rollback on failure: `PRAGMA
foreign_key_check` is empty afterwards and row identity (including
`phase_results.id`) is preserved. There is no downgrade migration, but a
previous release's `ResultsStore` still reads and writes a migrated
database correctly (the extra nullable `cost_mode` column does not break
its explicit-column `INSERT`s). Back up the database before upgrading
regardless.

This isolation is **results-database-only**. The cell's `workdir` path,
Docker container name, and gateway `trace_id` are still derived from
`cell_id` alone (`constraints.md` freezes the `cell_id` format), so
running two runs **concurrently** against the same `workdir` and
`gateway_db` with an overlapping `cell_id` still corrupts both — one
run's cell start can `rmtree` the other's live workdir, collide on the
container name, and delete the other's in-flight gateway calls.
Sequential runs, or runs with distinct `workdir`/`gateway_db` paths, are
unaffected.

## Run metadata

At the start of each run, the orchestrator saves run metadata:

```python
self.store.save_run_metadata(
    run_name=self.config.name,
    config_json=self.config.model_dump_json(indent=2),
    harness_evaluator_version=harness_evaluator.__version__,
    docker_image=self.config.docker_image,
)
```

This allows exact reproduction of a run: the config JSON can be written back to a YAML file, and the Docker image tag pins the harness versions.

## Dry run

Use `--dry-run` to print the matrix without executing:

```bash
harness-evaluator run runs/sample-run.yaml --dry-run
```

This prints a table with the first 20 cells (Cell ID, Harness, Model, Task, Repeat) and the total cell count.

## Gateway preflight check

Before executing, `harness-evaluator run` checks that the gateway proxy is reachable on the configured port:

```bash
# If gateway is not running:
$ harness-evaluator run runs/sample-minimal.yaml
Gateway is NOT reachable on 127.0.0.1:8877.
Start it in another terminal with:
  harness-evaluator gateway --port 8877
Then re-run this command.
```

Skip the check with `--no-check-gateway` (useful for testing or when the gateway runs on a different host).
