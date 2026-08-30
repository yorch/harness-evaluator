---
title: Reporting
description: Static reports (HTML/JSON/CSV), interactive FastAPI dashboard, and REST API endpoints.
---

# Reporting

harnessbench provides three ways to explore evaluation results: static reports (HTML/JSON/CSV), an interactive web dashboard, and a console results table.

## Static reports

Generate static reports with `harnessbench report`:

```bash
harnessbench report broad-first-pass --output ./reports
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
3. **Detailed results table**: every cell with harness, model, task, exit class, success, tokens, cost, time, error class

The HTML is generated with Jinja2 autoescaping enabled to prevent stored XSS from user-supplied identifiers (run names, cell IDs, etc.) stored in the database.

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

View results in the console with `harnessbench results`:

```bash
harnessbench results broad-first-pass
```

Prints a Rich table with columns: Harness, Model, Task, Exit, Success, Tokens, Cost, Time(s).

## Dashboard

Start the interactive dashboard with `harnessbench dashboard`:

```bash
harnessbench dashboard --port 8080
```

Then open `http://127.0.0.1:8080` in your browser.

> **Security**: The dashboard has no authentication. Keep it localhost-only by default. Do not expose it to external networks.

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
- **Leaderboards**: within-model harness comparison, sorted by success rate
- **Filtered results table**: filter by model, harness, task track, and minimum success rate
- **Pagination**: 50 results per page (configurable, max 500)

#### Filtering

| Filter | Description |
|--------|-------------|
| Model | Filter by model name |
| Harness | Filter by harness name |
| Track | Filter by task track (`swe` or `open_ended`) |
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

### SQL-level aggregation

The dashboard uses SQL-level aggregation queries (not loading the full table) for run summaries and counts. This keeps the dashboard responsive even with large result sets.

Paginated results use parameterized SQL queries with `LIMIT` and `OFFSET`. Column names in filter queries are validated against an allowlist (`model`, `harness`, `track`) to prevent SQL injection.

### Templates

Dashboard templates are in `src/harnessbench/dashboard/templates/`:

| Template | Description |
|----------|-------------|
| `index.html` | Run overview page |
| `run_detail.html` | Per-run detail with filtering and pagination |

All templates use Jinja2 with `select_autoescape(["html", "xml"])` for XSS safety.

## Key source files

| File | Description |
|------|-------------|
| `src/harnessbench/reporting/static_report.py` | `ReportGenerator` — HTML/JSON/CSV generation |
| `src/harnessbench/dashboard/app.py` | `create_app()` — FastAPI dashboard factory |
| `src/harnessbench/dashboard/templates/index.html` | Run overview template |
| `src/harnessbench/dashboard/templates/run_detail.html` | Run detail template |
