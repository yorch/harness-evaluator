---
title: CLI Reference
description: All harnessbench CLI commands, flags, and options with examples.
---

# CLI Reference

harnessbench uses [Typer](https://typer.tiangolo.com/) for its CLI. The entry point is `harnessbench` (defined in `pyproject.toml` as `harnessbench = "harnessbench.cli:app"`).

## Commands overview

| Command | Description |
|---------|-------------|
| [`harnessbench init`](#harnessbench-init) | Scaffold a starter run config (no clone needed) |
| [`harnessbench run`](#harnessbench-run) | Execute an evaluation run from a config file |
| [`harnessbench gateway`](#harnessbench-gateway) | Start the gateway proxy server |
| [`harnessbench canary`](#harnessbench-canary) | Verify proxy token capture accuracy |
| [`harnessbench report`](#harnessbench-report) | Generate static reports (HTML/JSON/CSV) |
| [`harnessbench results`](#harnessbench-results) | Show results summary in the console |
| [`harnessbench adapters`](#harnessbench-adapters) | List available harness adapters |
| [`harnessbench stats`](#harnessbench-stats) | Generate statistical analysis for a run |
| [`harnessbench dashboard`](#harnessbench-dashboard) | Start the interactive web dashboard |
| [`harnessbench calibrate`](#harnessbench-calibrate) | Run judge calibration against anchor set |

## harnessbench init

Scaffold a starter run config in the current directory so you can run harnessbench
without cloning the repository. The generated config uses the bundled task
library and the version-pinned published runner image by default.

### Usage

```bash
harnessbench init [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--filename` | string | `harnessbench.yaml` | Path for the generated config |
| `--force` / `--no-force` | flag | `False` | Overwrite an existing file |

### Examples

```bash
# Zero-install scaffold via uv (PyPI package: harnessbench)
uvx harnessbench init

# Custom filename, overwrite if present
harnessbench init --filename my-run.yaml --force
```

## harnessbench run

Execute an evaluation run from a YAML config file.

### Usage

```bash
harnessbench run <config> [options]
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

### Examples

```bash
# Dry run — print the matrix without executing
harnessbench run runs/sample-run.yaml --dry-run

# Minimal run (1 harness, 1 model, 1 task, 1 repeat)
harnessbench run runs/sample-minimal.yaml

# Full sweep (all 5 harnesses, 2 providers, all tasks)
harnessbench run runs/sample-run.yaml

# Skip gateway preflight check
harnessbench run runs/sample-run.yaml --no-check-gateway
```

### Output

```
Run: broad-first-pass
  Harnesses: ['opencode', 'claude-code', 'codex', 'pi', 'omp']
  Models: ['claude-sonnet-4-20250514', 'gpt-4o']
  Repeats: 5
  Total cells: 1000
Gateway reachable on port 8877

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

By default, `harnessbench run` checks that the gateway proxy is reachable on `127.0.0.1:<gateway_port>` before executing. If the gateway is not running:

```
Gateway is NOT reachable on 127.0.0.1:8877.
Start it in another terminal with:
  harnessbench gateway --port 8877
Then re-run this command.
```

## harnessbench gateway

Start the gateway proxy server for token accounting. See [Gateway Proxy](gateway-proxy/) for full details.

### Usage

```bash
harnessbench gateway [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--host` | string | `127.0.0.1` | Host to bind to |
| `--port` | int | `8877` | Port to bind to |
| `--db` | string | `harnessbench_gateway.db` | SQLite DB path for captured calls |

### Examples

```bash
# Start on default port
harnessbench gateway

# Custom host and port
harnessbench gateway --host 0.0.0.0 --port 8877

# Custom database path
harnessbench gateway --db /data/harnessbench_gateway.db
```

## harnessbench canary

Verify that the gateway proxy accurately captures token usage. Reads the last captured call from the gateway DB and compares proxy-captured usage against the provider's response.

### Usage

```bash
harnessbench canary [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harnessbench_gateway.db` | SQLite DB path |
| `--tolerance-pct` / `--tolerance` | float | `1.0` | Max allowed discrepancy percentage |

### Examples

```bash
# Default tolerance (1%)
harnessbench canary

# Stricter tolerance (0.5%)
harnessbench canary --tolerance-pct 0.5

# Custom DB path
harnessbench canary --db /data/harnessbench_gateway.db
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

## harnessbench report

Generate static reports (HTML, JSON, CSV) for a completed run.

### Usage

```bash
harnessbench report <run_name> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `run_name` | string | Yes | Name of the run to report on |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harnessbench_results.db` | Results DB path |
| `--output` | string | `./reports` | Output directory for reports |

### Examples

```bash
# Generate reports for a run
harnessbench report broad-first-pass

# Custom output directory
harnessbench report broad-first-pass --output ./my-reports

# Custom DB path
harnessbench report broad-first-pass --db /data/harnessbench_results.db
```

### Output

```
Reports generated:
  json: ./reports/broad-first-pass_report.json
  csv: ./reports/broad-first-pass_report.csv
  html: ./reports/broad-first-pass_report.html
```

See [Reporting](reporting/) for report format details.

## harnessbench results

Show results summary for a run in the console as a Rich table.

### Usage

```bash
harnessbench results <run_name> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `run_name` | string | Yes | Name of the run to show |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harnessbench_results.db` | Results DB path |

### Examples

```bash
harnessbench results broad-first-pass
harnessbench results minimal-first-run --db /data/harnessbench_results.db
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

## harnessbench adapters

List available harness adapters and their observability tiers.

### Usage

```bash
harnessbench adapters
```

No arguments or options.

### Output

See [Adapters](adapters/#listing-adapters) for example output.

## harnessbench stats

Generate statistical analysis for a run. See [Statistics](statistics/) for details on the models.

### Usage

```bash
harnessbench stats <run_name> [options]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `run_name` | string | Yes | Run name to analyze |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--db` | string | `harnessbench_results.db` | Results DB path |

### Examples

```bash
harnessbench stats broad-first-pass
harnessbench stats minimal-first-run --db /data/harnessbench_results.db
```

### Output

The command prints:
1. **Warnings** (if any) — small sample size, convergence issues
2. **Variance Decomposition** — harness/model/task/residual variance and percentages
3. **Mixed-Effects Model** — formula, R², coefficients with standard errors and p-values
4. **Bootstrap 95% CIs** — success rate by harness with confidence intervals
5. **Consistency Analysis** — per harness × model: mean, std, CV, N

See [Statistics](statistics/) for interpretation.

## harnessbench dashboard

Start the interactive web dashboard. See [Reporting](reporting/) for dashboard details.

### Usage

```bash
harnessbench dashboard [options]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--host` | string | `127.0.0.1` | Host to bind to |
| `--port` | int | `8080` | Port to bind to |
| `--db` | string | `harnessbench_results.db` | Results DB path |

### Examples

```bash
# Start on default port
harnessbench dashboard

# Custom port
harnessbench dashboard --port 3000

# Allow external connections (not recommended — no auth)
harnessbench dashboard --host 0.0.0.0
```

Then open `http://127.0.0.1:8080` in your browser.

## harnessbench calibrate

Run judge calibration against the anchor set. Verifies the frozen LLM judge produces consistent scores.

### Usage

```bash
harnessbench calibrate [options]
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
harnessbench calibrate

# Use a different judge model
harnessbench calibrate --model claude-opus-4-20250514
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
