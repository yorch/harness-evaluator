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

The eval matrix is the Cartesian product of harnesses × models × tasks × repeats:

```
RunConfig.build_matrix()
  │
  ├── expand_tasks()
  │   Load task YAMLs from task_library_path
  │   If tasks=["*"], use all tasks in the library
  │   Otherwise, resolve specific task IDs (validates they exist)
  │
  └── For each harness × model × task × repeat:
      Create RunCell(
          run_name, harness, model, task, repeat,
          cell_id = "{harness}__{model}__{task}__r{repeat}"
      )
```

For example, with 5 harnesses, 2 models, 20 tasks, and 5 repeats, the matrix has **1000 cells**.

### Cell ID format

Each cell has a unique ID: `{harness.name}__{model.name}__{task.id}__r{repeat}`

Example: `opencode__claude-sonnet-4-20250514__swe-bugfix-001__r0`

This ID is used as the `trace_id` for gateway proxy attribution, the Docker container name (sanitized), and the primary key in the results store.

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
   │  If remaining_budget < estimate → skip cell, mark as "skipped"
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

If `cell.budget` is set, that value is used as the estimate. Otherwise, the estimate is `budget_usd / total_cells` (using the real cell count from `build_matrix()`, not `len(config.tasks)` which is wrong when `tasks=["*"]`).

### Budget exhaustion

When the remaining budget is less than the estimated cell cost, the cell is skipped and marked with state `"skipped"` and reason `"Budget cap reached"`. The orchestrator logs a warning. After each cell completes, a post-update check warns if the total spend has exceeded the budget (can happen when a cell costs more than its reservation).

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

| Column | Type | Description |
|--------|------|-------------|
| `cell_id` | TEXT PK | Unique cell identifier |
| `run_name` | TEXT | Run name from config |
| `harness` | TEXT | Harness name |
| `model` | TEXT | Model name |
| `task_id` | TEXT | Task ID |
| `track` | TEXT | `swe` or `open_ended` |
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
| `timestamp` | TEXT | ISO timestamp |
| `retry_count` | INTEGER | Number of retries (0 = first attempt) |

### `run_state` table

Tracks cell execution state for resumability and live dashboard progress:

| Column | Type | Description |
|--------|------|-------------|
| `cell_id` | TEXT PK | Unique cell identifier |
| `run_name` | TEXT | Run name |
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
