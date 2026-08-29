---
title: Getting Started
description: Step-by-step guide to install heval, get the Docker image, set API keys, and run your first evaluation.
---

# Getting Started

This guide walks you through installing heval, getting the Docker image, configuring API keys, and running your first evaluation end-to-end.

## Prerequisites

- **Python 3.11+** (3.12 recommended)
- **Docker** — for running harnesses in isolated containers
- **API keys** — at least one of:
  - `ANTHROPIC_API_KEY` (for Claude models and Claude Code)
  - `OPENAI_API_KEY` (for GPT models and Codex)

## Quick start (no clone)

heval bundles its task library, so you can run it without cloning the
repository. Using [uv](https://docs.astral.sh/uv/):

```bash
# Scaffold a starter config in the current directory
uvx heval init

# Pull the pre-built runner image
docker pull ghcr.io/yorch/heval-runner:latest

# Set an API key
export ANTHROPIC_API_KEY=sk-ant-...

# Start the gateway (separate terminal)
uvx heval gateway --port 8877

# Run the evaluation
uvx heval run heval.yaml
```

`uvx heval` runs the published package in an ephemeral environment. To install
it persistently, use `uv tool install heval` or `pipx install heval`.

The rest of this guide covers the from-source workflow and explains each step
in detail.

## Step 1: Install dependencies

```bash
# Clone the repository
git clone https://github.com/yorch/heval.git
cd heval

# Install Python dependencies (including dev tools)
uv sync --extra dev
```

This installs all production and development dependencies: aiohttp, pydantic, typer, rich, fastapi, uvicorn, jinja2, pandas, statsmodels, httpx, pytest, ruff, mypy.

Verify the installation:

```bash
heval --help
```

You should see the list of available commands.

## Step 2: Get the Docker image

The runner executes harnesses inside a Docker container. The image contains all 5 harnesses + Python + Git.

You have two options: pull the pre-built image from GHCR (fastest) or build it locally.

### Option A: Pull the pre-built image from GHCR (recommended)

A pre-built image is published to the GitHub Container Registry on every push to `main`:

```bash
docker pull ghcr.io/yorch/heval-runner:latest
```

Then reference it in your run config:

```yaml
docker_image: "ghcr.io/yorch/heval-runner:latest"
```

Available tags:

| Tag | Description |
|-----|-------------|
| `latest` | Most recent build from `main` |
| `sha-<short-hash>` | Pinned to a specific commit |
| `main` | Alias for the latest `main` build |

> **Note**: The GHCR image is currently public. If the repository is private or the package visibility changes, you may need to authenticate first:
> ```bash
> echo "$GITHUB_TOKEN" | docker login ghcr.io -u <username> --password-stdin
> ```

### Option B: Build the image locally

```bash
docker build -t heval-runner:latest .
```

This takes ~5 minutes. The image is ~1.2 GB because it carries all five harnesses.

The default `docker_image` in run config is `heval-runner:latest`, so local builds work without any config change.

### Verify the image

```bash
docker run --rm ghcr.io/yorch/heval-runner:latest bash -c '
  echo "=== Harnesses ===" &&
  claude --version &&
  codex --version &&
  opencode --version &&
  pi --version &&
  omp --version &&
  echo "=== All harnesses ready ==="
'
```

> **Tip**: For a single-harness eval, you can build a trimmed variant by commenting out unused `RUN` lines in the Dockerfile.

## Step 3: Set API keys

```bash
# For Anthropic models (Claude)
export ANTHROPIC_API_KEY=sk-ant-...

# For OpenAI models (GPT)
export OPENAI_API_KEY=sk-...
```

You need at least one key for the model you plan to use. The key is passed into Docker containers via an allowlisted environment variable — the full host environment is never exposed.

## Step 4: Start the gateway proxy

The gateway proxy captures token usage, cost, and latency for every provider API call. Start it in a separate terminal:

```bash
heval gateway --port 8877
```

You should see:

```
Starting gateway proxy on 127.0.0.1:8877
Captured calls stored to: heval_gateway.db
Configure harnesses with:
  ANTHROPIC_BASE_URL=http://127.0.0.1:8877
  OPENAI_BASE_URL=http://127.0.0.1:8877
```

Keep this terminal open — the proxy must be running while you execute evals.

See [Gateway Proxy](gateway-proxy/) for full details.

## Step 5: Verify the proxy with canary

After starting the gateway, send a test request through it and verify token capture accuracy:

```bash
# In a new terminal, send a test request through the proxy
curl http://127.0.0.1:8877/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Say hello in one word."}]
  }'

# Verify token capture accuracy
heval canary --tolerance-pct 1.0
```

You should see:

```
Canary PASSED
Canary PASSED: proxy usage matches upstream response within 1.0% tolerance.
Tokens: in=42, out=5, cache_read=0, cache_write=0.
Cost: $0.000131. Latency: 523ms.
```

If the canary fails, see [Gateway Proxy](gateway-proxy/) for troubleshooting.

## Step 6: Dry-run your first eval

Before spending money, preview the eval matrix:

```bash
heval run runs/sample-minimal.yaml --dry-run
```

Output:

```
Run: minimal-first-run
  Harnesses: ['opencode']
  Models: ['claude-sonnet-4-20250514']
  Repeats: 1
  Total cells: 1

                          Eval Matrix
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Cell ID                                 ┃ Harness   ┃ Model              ┃ Task             ┃ Repeat ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ opencode__claude-sonnet-4-20250514__swe-bugfix-001__r0 │ opencode │ claude-sonnet-4-.. │ swe-bugfix-001 │ 0 │
└──────────────────────────────────────────┴───────────┴────────────────────┴──────────────────┴────────┘
```

This confirms the config is valid and shows exactly what will run.

## Step 7: Run the minimal eval

```bash
heval run runs/sample-minimal.yaml
```

This runs one cell: OpenCode with Claude Sonnet on the `swe-bugfix-001` task, one repeat. Budget cap is $5.

Output:

```
Run: minimal-first-run
  Harnesses: ['opencode']
  Models: ['claude-sonnet-4-20250514']
  Repeats: 1
  Total cells: 1
Gateway reachable on port 8877

Run complete
  Passed: 1
  Failed: 0
  Skipped: 0
  Cost: $0.0037
```

## Step 8: View results

### Console

```bash
heval results minimal-first-run
```

### Static reports

```bash
heval report minimal-first-run --output ./reports
```

This generates three files:
- `reports/minimal-first-run_report.html` — styled HTML report
- `reports/minimal-first-run_report.json` — machine-readable JSON
- `reports/minimal-first-run_report.csv` — flat CSV

Open the HTML report in a browser to see leaderboards and detailed results.

### Dashboard

```bash
heval dashboard --port 8080
```

Open `http://127.0.0.1:8080` to explore results interactively. See [Reporting](reporting/) for dashboard features.

### Statistics

```bash
heval stats minimal-first-run
```

See [Statistics](statistics/) for interpretation of the output.

## Step 9: Run a full sweep

Once the minimal run works, try the full sweep:

```bash
heval run runs/sample-run.yaml
```

This runs all 5 harnesses × 2 models × all tasks × 5 repeats. With 20 tasks, that's 1000 cells. Budget cap is $100.

> **Warning**: The full sweep can take hours and cost significant money. Start with a small budget and fewer repeats to validate before scaling up.

## Step 10: Run judge calibration (open-ended track)

If you're running open-ended tasks, calibrate the LLM judge first:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
heval calibrate --model claude-sonnet-4-20250514
```

This verifies the judge produces consistent scores against known anchor submissions. If calibration fails (MAE > 0.15), the open-ended track should be flagged as unreliable.

See [Evaluators](evaluators/#calibration) for details.

## Creating a custom run config

Create a YAML file for your own eval:

```yaml
name: "my-eval"
description: "My custom evaluation"
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
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
tasks:
  - "swe-bugfix-001"
  - "swe-bugfix-002"
  - "open-design-001"
task_library_path: "./tasks"
repeats: 3
budget_usd: 20.0
```

See [Configuration](configuration/) for the full schema.

## Troubleshooting

### Gateway not reachable

```
Gateway is NOT reachable on 127.0.0.1:8877.
```

Start the gateway in a separate terminal: `heval gateway --port 8877`

### No API calls found for trace_id

```
WARNING: No API calls found with trace_id=... for cell ...; cost attribution will be zero.
```

This means the harness is not routing through the gateway proxy. Common with minimal-observability harnesses (Pi, OMP) that may bypass the proxy. For partial-observability harnesses, check that the adapter's `get_env()` is setting the correct base URL.

### Docker image not found

```
docker run failed (exit 1): Unable to find image 'heval-runner:latest' locally
```

Either pull the pre-built image or build it locally:

```bash
docker pull ghcr.io/yorch/heval-runner:latest   # pre-built
docker build -t heval-runner:latest .            # local build
```

If using the GHCR image, set `docker_image: "ghcr.io/yorch/heval-runner:latest"` in your run config.

### No pricing found for model

```
WARNING: No pricing found for model 'my-model'; cost will be $0
```

Add the model to `DEFAULT_PRICING` in `src/heval/gateway/models.py`. See [Configuration](configuration/#pricing-tables).

### Harness binary not found in container

The harness command fails with "command not found" inside the container. Verify the Docker image includes the harness:

```bash
docker run --rm heval-runner:latest <harness> --version
```

If missing, rebuild the image or check the Dockerfile.

### Budget cap reached

Cells are being skipped with reason "Budget cap reached". Either increase `budget_usd` in the config or reduce the number of cells (fewer harnesses, models, tasks, or repeats).
