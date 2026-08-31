---
title: Reporting
description: Static reports (HTML/JSON/CSV), interactive FastAPI dashboard, and REST API endpoints.
---

# Reporting

harness-evaluator provides three ways to explore evaluation results: static reports (HTML/JSON/CSV), an interactive web dashboard, and a console results table.

## Static reports

Generate static reports with `harness-evaluator report`:

```bash
harness-evaluator report broad-first-pass --output ./reports
```

### Output files

| Format | File | Description |
|--------|------|-------------|
| HTML | `{run_name}_report.html` | Styled report with summary cards, leaderboards, and detailed results table |
| JSON | `{run_name}_report.json` | Machine-readable report with leaderboards and all results |
| CSV | `{run_name}_report.csv` | Flat CSV with all result fields for spreadsheet analysis |

### HTML report

The HTML report includes:

1. **Summary cards**: total cells, passed, failed, total cost, average success rate
2. **Within-model leaderboards**: one table per model, sorted by success rate descending
3. **Detailed results table**: every cell with harness, model, task, exit class, success, tokens, cost, time, error class, error message

The HTML is generated with Jinja2 autoescaping enabled to prevent stored XSS from user-supplied identifiers (run names, cell IDs, error messages, etc.) stored in the database.

### JSON report structure

```json
{
  "run_name": "broad-first-pass",
  "timestamp": "2024-01-15T12:34:56.789+00:00",
  "total_cells": 300,
  "leaderboards": {
    "claude-sonnet-4-20250514": [
      {
        "harness": "opencode",
        "success_pct": "85.0",
        "success_class": "pass",
        "avg_tokens": "1234",
        "avg_cost": "0.003702",
        "avg_time_s": "12.3",
        "avg_api_calls": "5.2"
      }
    ]
  },
  "results": [
    {
      "cell_id": "opencode__claude-sonnet-4-...__r0",
      "harness": "opencode",
      "model": "claude-sonnet-4-20250514",
      "task_id": "swe-bugfix-001",
      "exit_class": "pass",
      "success": 1.0,
      "total_cost": 0.003702,
      ...
    }
  ]
}
```

### CSV report fields

The CSV includes all `run_results` columns:

`cell_id`, `run_name`, `harness`, `model`, `task_id`, `track`, `repeat`, `exit_class`, `success`, `error_class`, `error_message`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `total_cost`, `latency_ms`, `time_to_first_attempt_ms`, `num_api_calls`, `num_tool_calls`, `diff`, `test_output`, `harness_metadata`, `timestamp`, `retry_count`

For `multi_phase` cells, the `harness_metadata` JSON column includes:

- `phases`: list of per-phase dicts (`name`, `trace_id`, `model`, `model_role`, `exit_code`, `duration_ms`, `timed_out`, `usage`, `total_cost`, `num_api_calls`)
- `review_model`: the adversarial reviewer model name (or `null`)

Per-phase cost and token breakdowns are also available in the `phase_results` SQLite table — see [Orchestrator → phase-results table](orchestrator/#phase-results-table) for the schema and query examples.

### Leaderboard computation

Leaderboards are **within-model**: each model gets its own table. For each harness within a model:

- **Success rate**: average `success` across all cells for that harness × model
- **Avg tokens**: average total tokens (input + output + cache_read + cache_write + reasoning)
- **Avg cost**: average `total_cost`
- **Avg time**: average `latency_ms` converted to seconds
- **Avg API calls**: average `num_api_calls`

Rows are sorted by success rate descending. Success rate is color-coded:
- ≥ 80%: green (pass)
- ≥ 50%: orange (partial)
- < 50%: red (fail)

### Path traversal protection

Run names are sanitized before use in filenames (`sanitize_id()`), and output paths are validated against the output directory (`assert_safe_path()`). This prevents path traversal via `../` in user-supplied run names.

## Console results

View results in the console with `harness-evaluator results`:

```bash
harness-evaluator results broad-first-pass
```

Prints a Rich table with columns: Harness, Model, Task, Exit, Success,
Tokens, Cost, Time(s), Error Class, Error Message. Long error messages
are truncated to 60 characters with an ellipsis (`…`).

If no run name is given, lists all runs in the database with aggregate
stats (cells, completed, failed, avg success, total cost).

## Dashboard

Start the interactive dashboard with `harness-evaluator dashboard`:

```bash
harness-evaluator dashboard --port 8080
```

Then open `http://127.0.0.1:8080` in your browser.

### Network access with token authentication

By default the dashboard binds to `127.0.0.1` (localhost only) and requires
no authentication. To expose it to other devices on your network, use
`--host 0.0.0.0` together with `--token`:

```bash
harness-evaluator dashboard --host 0.0.0.0 --port 8080 --token my-secret-token
```

Then open `http://<your-ip>:8080/login` from any device on the network and
enter the token. This sets an `HttpOnly` session cookie and redirects to
the dashboard. Subsequent navigation works without the token in the URL.

API clients can use the `Authorization: Bearer` header instead:

```bash
curl -H "Authorization: Bearer my-secret-token" http://<your-ip>:8080/api/runs
```

To avoid exposing the token in the process list (`ps aux`), use the
`HARNESS_EVALUATOR_DASHBOARD_TOKEN` environment variable instead of
`--token`:

```bash
export HARNESS_EVALUATOR_DASHBOARD_TOKEN=my-secret-token
harness-evaluator dashboard --host 0.0.0.0
```

> **Security**: Token comparison uses SHA-256 + `hmac.compare_digest` to
> prevent timing attacks and avoid leaking the token length. When auth is
> enabled, uvicorn access logs are disabled (the `?token=` query param
> would otherwise leak the token to logs), and the `/docs`, `/redoc`, and
> `/openapi.json` endpoints are disabled. Binding to `0.0.0.0` without
> `--token` prints a warning and is not recommended.

### Features

#### Run overview (home page)

Lists all runs in the results database with summary stats:

| Column | Description |
|--------|-------------|
| Run name | Run identifier |
| Total cells | Number of cells in the run |
| Passed | Cells with exit_class=pass |
| Failed | Cells with exit_class=fail |
| Total cost | Cumulative spend |
| Avg success | Average success rate |

#### Run detail page

Per-run view with:

- **Summary stats**: total cells, passed, cost
- **Live progress**: from `run_state` table (shows running/completed/failed/skipped counts if a run is in progress)
- **Failed / Skipped Cells section**: lists cells from both `run_state` (failed/skipped) and `run_results` (exit_class != 'pass') with their error messages, so you can see why cells failed without scrolling through the full results table
- **Leaderboards**: within-model harness comparison, sorted by success rate
- **Filtered results table**: filter by model, harness, task track, and minimum success rate; columns include Error Class and Error Message (truncated with hover-to-view full text)
- **Sortable columns**: click any column header to sort ascending or descending
- **Pagination**: 50 results per page (configurable, max 500)
- **Phase Details**: collapsible (`<details>`) per-cell phase tables for multi-phase tasks, showing phase name, model, role, exit code, duration, timeout status, tokens, cost, and per-phase errors. Phase results are loaded only for the current page's cells for performance
- **Dark mode**: automatic via `prefers-color-scheme`, with a manual toggle in the page header
- **CSV/JSON export**: download the filtered results as CSV or JSON via the export buttons

#### Cell detail page

Each cell in the results table links to a dedicated cell detail page
(`/run/{run_name}/cell/{cell_id}`) showing:

- Full cell metadata (harness, model, task, exit class, success, cost, tokens, timing)
- Error class and error message
- Git diff of changes (with syntax highlighting)
- Test output
- Phase results (for multi-phase cells)
- Reconciliation results (if available)

#### Filtering

| Filter | Description |
|--------|-------------|
| Model | Filter by model name |
| Harness | Filter by harness name |
| Track | Filter by task track (`swe`, `open_ended`, or `multi_phase`) |
| Min success | Only show cells with success ≥ this value |

Filter dropdowns are populated from the actual data in the results database (unique values per column).

### REST API

The dashboard exposes JSON API endpoints for programmatic access:

#### `GET /api/runs`

List all runs with summary stats.

```json
{
  "runs": [
    {
      "run_name": "broad-first-pass",
      "total_cells": 300,
      "passed": 180,
      "failed": 120,
      "total_cost": 12.3456,
      "avg_success": 0.6
    }
  ]
}
```

#### `GET /api/run/{run_name}`

Get filtered, paginated results for a run.

Query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | null | Filter by model |
| `harness` | string | null | Filter by harness |
| `track` | string | null | Filter by task track |
| `min_success` | float | null | Minimum success rate |
| `page` | int | 1 | Page number (≥1) |
| `per_page` | int | 50 | Results per page (1–500) |

```json
{
  "run_name": "broad-first-pass",
  "page": 1,
  "per_page": 50,
  "total": 300,
  "total_pages": 6,
  "count": 50,
  "results": [...]
}
```

#### `GET /api/run/{run_name}/leaderboard`

Get leaderboard data for a run.

```json
{
  "run_name": "broad-first-pass",
  "leaderboards": {
    "claude-sonnet-4-20250514": [...],
    "gpt-4o": [...]
  }
}
```

#### `GET /api/run/{run_name}/status`

Get live progress for a run (from `run_state` table).

```json
{
  "run_name": "broad-first-pass",
  "state": {
    "completed": 150,
    "running": 2,
    "failed": 10,
    "skipped": 0
  }
}
```

#### `GET /api/run/{run_name}/errors`

Get failed and skipped cells with error messages for a run. Combines
`run_state` (failed/skipped) and `run_results` (exit_class != 'pass')
entries, deduplicated by cell ID.

```json
{
  "run_name": "broad-first-pass",
  "failed_cells": [
    {
      "cell_id": "claude-code__claude-sonnet-4__swe-001__r0",
      "status": "failed",
      "error": "crash: Segmentation fault"
    },
    {
      "cell_id": "opencode__gpt-4o__swe-003__r2",
      "status": "skipped",
      "error": "Budget cap reached ($0.0123 remaining < $0.0500 estimated)"
    }
  ]
}
```

Returns 404 if the run name is not found.

#### `GET /run/{run_name}/export/{format}`

Export filtered results as a downloadable file. The `format` path
parameter must be `csv` or `json`. Accepts the same filter query
parameters as `/api/run/{run_name}` (`model`, `harness`, `track`,
`min_success`, `sort`, `order`).

### SQL-level aggregation

The dashboard uses SQL-level aggregation queries (not loading the full table) for run summaries and counts. This keeps the dashboard responsive even with large result sets.

Paginated results use parameterized SQL queries with `LIMIT` and `OFFSET`. Column names in filter queries are validated against an allowlist (`model`, `harness`, `track`) to prevent SQL injection.

### Templates

Dashboard templates are in `src/harness_evaluator/dashboard/templates/`:

| Template | Description |
|----------|-------------|
| `_base.html` | Shared layout with dark mode support, theme toggle, and accessibility landmarks |
| `index.html` | Run overview page |
| `run_detail.html` | Per-run detail with filtering, sorting, pagination, failed cells, and phase details |
| `cell_detail.html` | Per-cell detail with diff, test output, phases, and reconciliation |

All templates use Jinja2 with `select_autoescape(["html", "xml"])` for XSS safety. Error messages, run names, cell IDs, and all other user-supplied values are escaped.

## Key source files

| File | Description |
|------|-------------|
| `src/harness_evaluator/reporting/static_report.py` | `ReportGenerator` — HTML/JSON/CSV generation |
| `src/harness_evaluator/dashboard/app.py` | `create_app()` — FastAPI dashboard factory |
| `src/harness_evaluator/dashboard/templates/_base.html` | Shared layout template |
| `src/harness_evaluator/dashboard/templates/index.html` | Run overview template |
| `src/harness_evaluator/dashboard/templates/run_detail.html` | Run detail template |
| `src/harness_evaluator/dashboard/templates/cell_detail.html` | Cell detail template |
