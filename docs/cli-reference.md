---
title: CLI Reference
description: All harness-evaluator CLI commands, flags, and options with examples.
---

# CLI Reference

harness-evaluator uses [Typer](https://typer.tiangolo.com/) for its CLI. The entry point is `harness-evaluator` (defined in `pyproject.toml` as `harness-evaluator = "harness_evaluator.cli:app"`).

## Commands overview

| Command | Description |
|---------|-------------|
| [`harness-evaluator init`](#harness-evaluator-init) | Scaffold a starter run config (no clone needed) |
| [`harness-evaluator run`](#harness-evaluator-run) | Execute an evaluation run from a config file |
| [`harness-evaluator gateway`](#harness-evaluator-gateway) | Start the gateway proxy server |
| [`harness-evaluator canary`](#harness-evaluator-canary) | Verify proxy token capture accuracy |
| [`harness-evaluator report`](#harness-evaluator-report) | Generate static reports (HTML/JSON/CSV) |
| [`harness-evaluator results`](#harness-evaluator-results) | Show results summary in the console |
| [`harness-evaluator adapters`](#harness-evaluator-adapters) | List available harness adapters |
| [`harness-evaluator stats`](#harness-evaluator-stats) | Generate statistical analysis for a run |
| [`harness-evaluator dashboard`](#harness-evaluator-dashboard) | Start the interactive web dashboard |
| [`harness-evaluator calibrate`](#harness-evaluator-calibrate) | Run judge calibration against anchor set |

## harness-evaluator init

Scaffold a starter run config in the current directory so you can run harness-evaluator
without cloning the repository. The generated config uses the bundled task
library and the version-pinned published runner image by default.

### Usage

```bash
harness-evaluator init [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--filename` | string | `harness-evaluator.yaml` | Path for the generated config |
| `--force` / `--no-force` | flag | `False` | Overwrite an existing file |

### Examples

```bash
# Zero-install scaffold via uv (PyPI package: harness-evaluator)
uvx harness-evaluator init

# Custom filename, overwrite if present
harness-evaluator init --filename my-run.yaml --force
```

## harness-evaluator run

Execute an evaluation run from a YAML config file.

### Usage

```bash
harness-evaluator run <config> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `config` | string | Yes | Path to run config YAML file |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--dry-run` | flag | `False` | Print the eval matrix without executing |
| `--check-gateway` / `--no-check-gateway` | flag | `True` | Preflight: check that the gateway is reachable |
| `--verbose` / `-v` | count | `0` | Increase logging verbosity (`-v`=INFO, `-vv`=DEBUG) |
| `--progress` / `--no-progress` | flag | `True` | Show a live progress panel during the run (auto-off in non-TTY) |

### Examples

```bash
# Dry run — print the matrix without executing
harness-evaluator run runs/sample-run.yaml --dry-run

# Minimal run (1 harness, 1 model, 1 task, 1 repeat)
harness-evaluator run runs/sample-minimal.yaml

# Full sweep (all 5 harnesses, 2 providers, all tasks)
harness-evaluator run runs/sample-run.yaml

# Skip gateway preflight check
harness-evaluator run runs/sample-run.yaml --no-check-gateway

# Disable the live progress panel (e.g. for CI logs)
harness-evaluator run runs/sample-run.yaml --no-progress

# Show per-cell INFO logs (retries, budget, gateway calls)
harness-evaluator run runs/sample-run.yaml -v

# Show DEBUG-level detail (adapter/docker internals)
harness-evaluator run runs/sample-run.yaml -vv
```

### Output

```
Run: broad-first-pass
  Harnesses: ['opencode', 'claude-code', 'codex', 'pi', 'omp']
  Models: ['claude-sonnet-4-20250514', 'gpt-4o']
  Repeats: 5
  Total cells: 1000
Gateway reachable on port 8877
```

During the run, a live progress panel is shown (auto-off in non-TTY/CI):

```
╭─────────────────────── Eval Progress ───────────────────────╮
│ ████████████░░░░░░░░░░░░░░░░░░░  120/1000 (12.0%)          │
│ ✓ 100  ✗ 15  ⊘ 5  ► 1                                       │
│ Cost: $1.2340 / $100.00  |  Elapsed: 342s                   │
│ Running: opencode__claude-sonnet-4-...__swe-bugfix-003__r0  │
╰─────────────────────────────────────────────────────────────╯
```

The panel shows: a progress bar, completed/failed/skipped/running counts,
cumulative cost (with budget cap if set), elapsed time, and the current
cell ID (or running count for parallel runs). Use `--no-progress` to
disable it, or `-v`/`-vv` to add per-cell log lines alongside it.

```
Run complete
  Passed: 600
  Failed: 400
  Skipped: 0
  Cost: $12.3456
```

### Dry run output

```
Run: broad-first-pass
  Harnesses: ['opencode', 'claude-code', 'codex', 'pi', 'omp']
  Models: ['claude-sonnet-4-20250514', 'gpt-4o']
  Repeats: 5
  Total cells: 1000

                          Eval Matrix
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Cell ID                              ┃ Harness   ┃ Model                ┃ Task             ┃ Repeat ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ opencode__claude-sonnet-4-...__swe-bugfix-001__r0 │ opencode │ claude-sonnet-4-... │ swe-bugfix-001 │ 0 │
│ opencode__claude-sonnet-4-...__swe-bugfix-001__r1 │ opencode │ claude-sonnet-4-... │ swe-bugfix-001 │ 1 │
│ ...                                  │ ...       │ ...                  │ ...              │ ...    │
└──────────────────────────────────────┴───────────┴──────────────────────┴──────────────────┴────────┘
```

### Gateway preflight

By default, `harness-evaluator run` checks that the gateway proxy is reachable on `127.0.0.1:<gateway_port>` before executing. If the gateway is not running:

```
Gateway is NOT reachable on 127.0.0.1:8877.
Start it in another terminal with:
  harness-evaluator gateway --port 8877
Then re-run this command.
```

## harness-evaluator gateway

Start the gateway proxy server for token accounting. See [Gateway Proxy](gateway-proxy/) for full details.

### Usage

```bash
harness-evaluator gateway [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--host` | string | `127.0.0.1` | Host to bind to |
| `--port` | int | `8877` | Port to bind to |
| `--db` | string | `harness_evaluator_gateway.db` | SQLite DB path for captured calls |
| `--verbose` / `-v` | count | `0` | Increase logging verbosity (`-v`=INFO, `-vv`=DEBUG) |

### Examples

```bash
# Start on default port
harness-evaluator gateway

# Custom host and port
harness-evaluator gateway --host 0.0.0.0 --port 8877

# Custom database path
harness-evaluator gateway --db /data/harness_evaluator_gateway.db

# Show per-call INFO logs (model, tokens, cost per captured call)
harness-evaluator gateway -v
```

## harness-evaluator canary

Verify that the gateway proxy accurately captures token usage. Reads the last captured call from the gateway DB and compares proxy-captured usage against the provider's response.

### Usage

```bash
harness-evaluator canary [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harness_evaluator_gateway.db` | SQLite DB path |
| `--tolerance-pct` / `--tolerance` | float | `1.0` | Max allowed discrepancy percentage |

### Examples

```bash
# Default tolerance (1%)
harness-evaluator canary

# Stricter tolerance (0.5%)
harness-evaluator canary --tolerance-pct 0.5

# Custom DB path
harness-evaluator canary --db /data/harness_evaluator_gateway.db
```

### Output

```
Canary PASSED
Canary PASSED: proxy usage matches upstream response within 1.0% tolerance.
Tokens: in=42, out=87, cache_read=0, cache_write=0.
Cost: $0.001449. Latency: 523ms.
```

For streaming responses (where the proxy is the source of truth):

```
Canary PASSED
Canary PASSED (single source): only proxy usage available.
Tokens: 129. This is expected for streaming responses.
```

## harness-evaluator report

Generate static reports (HTML, JSON, CSV) for a completed run.

### Usage

```bash
harness-evaluator report <run_name> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `run_name` | string | Yes | Name of the run to report on |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harness_evaluator_results.db` | Results DB path |
| `--output` | string | `./reports` | Output directory for reports |

### Examples

```bash
# Generate reports for a run
harness-evaluator report broad-first-pass

# Custom output directory
harness-evaluator report broad-first-pass --output ./my-reports

# Custom DB path
harness-evaluator report broad-first-pass --db /data/harness_evaluator_results.db
```

### Output

```
Reports generated:
  json: ./reports/broad-first-pass_report.json
  csv: ./reports/broad-first-pass_report.csv
  html: ./reports/broad-first-pass_report.html
```

See [Reporting](reporting/) for report format details.

## harness-evaluator results

Show results summary for a run in the console as a Rich table.

### Usage

```bash
harness-evaluator results <run_name> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `run_name` | string | Yes | Name of the run to show |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harness_evaluator_results.db` | Results DB path |

### Examples

```bash
harness-evaluator results broad-first-pass
harness-evaluator results minimal-first-run --db /data/harness_evaluator_results.db
```

### Output

```
                      Results: broad-first-pass
┏━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┓
┃ Harness   ┃ Model              ┃ Task             ┃ Exit   ┃ Success ┃ Tokens  ┃ Cost     ┃ Time(s) ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━┩
│ opencode  │ claude-sonnet-4-.. │ swe-bugfix-001   │ pass   │ 1.00    │ 1234    │ $0.0037  │ 12.3    │
│ claude-c. │ claude-sonnet-4-.. │ swe-bugfix-001   │ fail   │ 0.00    │ 5678    │ $0.0170  │ 45.6    │
└───────────┴────────────────────┴──────────────────┴────────┴─────────┴─────────┴──────────┴─────────┘
```

## harness-evaluator adapters

List available harness adapters and their observability tiers.

### Usage

```bash
harness-evaluator adapters
```

No arguments or options.

### Output

See [Adapters](adapters/#listing-adapters) for example output.

## harness-evaluator stats

Generate statistical analysis for a run. See [Statistics](statistics/) for details on the models.

### Usage

```bash
harness-evaluator stats <run_name> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `run_name` | string | Yes | Run name to analyze |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harness_evaluator_results.db` | Results DB path |

### Examples

```bash
harness-evaluator stats broad-first-pass
harness-evaluator stats minimal-first-run --db /data/harness_evaluator_results.db
```

### Output

The command prints:
1. **Warnings** (if any) — small sample size, convergence issues
2. **Variance Decomposition** — harness/model/task/residual variance and percentages
3. **Mixed-Effects Model** — formula, R², coefficients with standard errors and p-values
4. **Bootstrap 95% CIs** — success rate by harness with confidence intervals
5. **Consistency Analysis** — per harness × model: mean, std, CV, N

See [Statistics](statistics/) for interpretation.

## harness-evaluator dashboard

Start the interactive web dashboard. See [Reporting](reporting/) for dashboard details.

### Usage

```bash
harness-evaluator dashboard [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--host` | string | `127.0.0.1` | Host to bind to |
| `--port` | int | `8080` | Port to bind to |
| `--db` | string | `harness_evaluator_results.db` | Results DB path |

### Examples

```bash
# Start on default port
harness-evaluator dashboard

# Custom port
harness-evaluator dashboard --port 3000

# Allow external connections (not recommended — no auth)
harness-evaluator dashboard --host 0.0.0.0
```

Then open `http://127.0.0.1:8080` in your browser.

## harness-evaluator calibrate

Run judge calibration against the anchor set. Verifies the frozen LLM judge produces consistent scores.

### Usage

```bash
harness-evaluator calibrate [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--model` | string | `claude-sonnet-4-20250514` | Judge model |

### Prerequisites

Requires `ANTHROPIC_API_KEY` environment variable to be set.

### Examples

```bash
export ANTHROPIC_API_KEY=sk-ant-...
harness-evaluator calibrate

# Use a different judge model
harness-evaluator calibrate --model claude-opus-4-20250514
```

### Output

```
Running calibration...

Judge version: v1.0
Anchors: 2
Mean Absolute Error: 0.0234
Drift detected: No
Reliable: Yes
  perfect: expected=1.00 actual=0.98 OK
  minimal: expected=0.25 actual=0.27 OK
```

If drift is detected (MAE > 0.15), the open-ended track should be flagged as unreliable for that run.
