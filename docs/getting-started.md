---
title: Getting Started
description: Install harness-evaluator from PyPI, pull the Docker image, set API keys, and run your first evaluation — no clone required.
---

# Getting Started

This guide walks you through installing harness-evaluator from PyPI, getting the Docker image, configuring API keys, and running your first evaluation end-to-end. **No clone required** — the task library is bundled into the wheel.

## Prerequisites

- **Python 3.11+** (3.12 recommended)
- **Docker** — for running harnesses in isolated containers
- **API keys** — at least one of:
  - `ANTHROPIC_API_KEY` (for Claude models and Claude Code)
  - `OPENAI_API_KEY` (for GPT models and Codex)

## Quick start (no clone)

harness-evaluator is published on PyPI as `harness-evaluator`. It bundles its task library, so you can run it without cloning the repository.

### Step 1: Install

Use [uv](https://docs.astral.sh/uv/) (recommended) to run it without installing:

```bash
# Run without installing (ephemeral environment per invocation)
uvx harness-evaluator --help

# Or install persistently
uv tool install harness-evaluator
# Alternative: pipx install harness-evaluator
```

Both `uvx` and `uv tool install` provide the `harness-evaluator` command. If you don't have `uv`, install it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

You can also install with pip:

```bash
pip install harness-evaluator
```

### Step 2: Scaffold a config

```bash
uvx harness-evaluator init
```

This creates `harness-evaluator.yaml` in the current directory with a minimal starter config (1 harness, 1 model, 1 task, 1 repeat, $5 budget).

### Step 3: Pull the Docker image

The runner executes harnesses inside a Docker container. The image contains all 11 harnesses + Python + Git:

```bash
docker pull ghcr.io/yorch/harness-evaluator-runner:latest
```

Available tags:

| Tag | Description |
|-----|-------------|
| `latest` | Most recent build from `main` |
| `sha-<short-hash>` | Pinned to a specific commit |
| `main` | Alias for the latest `main` build |
| `<version>` | Pinned to a release (e.g. `0.2.0`) |

> **Note**: The GHCR image is public. If the repository visibility changes, you may need to authenticate first:
> ```bash
> echo "$GITHUB_TOKEN" | docker login ghcr.io -u <username> --password-stdin
> ```

### Step 4: Set API keys

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# Optional: export OPENAI_API_KEY=sk-...
```

The key is passed into Docker containers via an allowlisted environment variable — the full host environment is never exposed.

### Step 5: Start the gateway proxy

The gateway proxy captures token usage, cost, and latency for every provider API call. Start it in a separate terminal:

```bash
uvx harness-evaluator gateway --port 8877
```

You should see:

```
Starting gateway proxy on 127.0.0.1:8877
Captured calls stored to: harness_evaluator_gateway.db
Configure harnesses with:
  ANTHROPIC_BASE_URL=http://127.0.0.1:8877
  OPENAI_BASE_URL=http://127.0.0.1:8877
```

Keep this terminal open — the proxy must be running while you execute evals.

See [Gateway Proxy](gateway-proxy/) for full details.

### Step 6: Verify the proxy with canary

After starting the gateway, verify token capture accuracy:

```bash
uvx harness-evaluator canary --tolerance-pct 1.0
```

You should see:

```
Canary PASSED
Canary PASSED: proxy usage matches upstream response within 1.0% tolerance.
Tokens: in=42, out=5, cache_read=0, cache_write=0.
Cost: $0.000131. Latency: 523ms.
```

If the canary fails, see [Gateway Proxy](gateway-proxy/) for troubleshooting.

### Step 7: Dry-run your first eval

Preview the eval matrix without spending money:

```bash
uvx harness-evaluator run harness-evaluator.yaml --dry-run
```

Output:

```
Run: minimal-first-run
  Harnesses: ['opencode']
  Models: ['claude-sonnet-4-20250514']
  Repeats: 1
  Total cells: 1

                          Eval Matrix
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ Cell ID                                 ┃ Harness   ┃ Model              ┃ Task             ┃ Repeat ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ opencode__claude-sonnet-4-20250514__swe-bugfix-001__r0 │ opencode │ claude-sonnet-4-.. │ swe-bugfix-001 │ 0 │
└──────────────────────────────────────────┴───────────┴────────────────────┴──────────────────┴────────┘
```

This confirms the config is valid and shows exactly what will run.

### Step 8: Run the eval

```bash
uvx harness-evaluator run harness-evaluator.yaml
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

Next steps
  View per-cell results:
    harness-evaluator results minimal-first-run
  Generate HTML/JSON/CSV reports:
    harness-evaluator report minimal-first-run
  Statistical analysis:
    harness-evaluator stats minimal-first-run
  Interactive dashboard:
    harness-evaluator dashboard --db harness_evaluator_results.db
```

The run name (`minimal-first-run`) comes from the `name:` field in your
config YAML, not the filename. To list all runs in the database, run
`harness-evaluator results` with no argument.

### Step 9: View results

The "Next steps" section at the end of the run output shows the exact
commands to use. You can also discover them at any time:

```bash
# List all runs in the database (useful if you forgot the run name)
uvx harness-evaluator results

# Console summary of a specific run
uvx harness-evaluator results minimal-first-run

# Static HTML/JSON/CSV report
uvx harness-evaluator report minimal-first-run --output ./reports

# Interactive dashboard
uvx harness-evaluator dashboard --port 8080

# Statistical analysis (mixed-effects model, variance decomposition)
uvx harness-evaluator stats minimal-first-run
```

Open the HTML report in a browser to see leaderboards and detailed results.
Open `http://127.0.0.1:8080` to explore results interactively.

See [Reporting](reporting/) for dashboard features and [Statistics](statistics/) for interpretation of the output.

## From source (optional)

If you want to contribute, modify the codebase, or build the Docker image locally:

```bash
# Clone the repository
git clone https://github.com/yorch/harness-evaluator.git
cd harness-evaluator

# Install Python dependencies (including dev tools)
uv sync --extra dev

# Verify the installation
harness-evaluator --help

# Build the Docker image locally (alternative to pulling from GHCR)
docker build -t harness-evaluator-runner:latest .
```

When running from source, you can use the bundled sample configs:

```bash
# Minimal: 1 harness, 1 model, 1 task, 1 repeat
harness-evaluator run runs/sample-minimal.yaml

# Full sweep: 11 harnesses × 2 models × 20 tasks × 5 repeats
harness-evaluator run runs/sample-run.yaml
```

> **Warning**: The full sweep can take hours and cost significant money. Start with a small budget and fewer repeats to validate before scaling up.

See [Development](development/) for contribution guidelines and the dev workflow.

## Judge calibration (open-ended track)

If you're running open-ended tasks, calibrate the LLM judge first:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uvx harness-evaluator calibrate --model claude-sonnet-4-20250514
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

## Running with a subscription (Claude Code OAuth / Codex ChatGPT)

If you have a Claude Pro/Max or ChatGPT subscription, you can run Claude Code or
Codex against your subscription instead of pay-per-token API keys. Token usage is
still captured for analysis, but cost is recorded as `$0` and does not count
against `budget_usd`.

Set `auth_mode` and `credentials_path` on the model, and `cost_mode: subscription`:

```yaml
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    auth_mode: claude_oauth
    credentials_path: "~/.claude/.credentials.json"
    cost_mode: subscription
```

For the full setup — obtaining the OAuth credential files, the Codex ChatGPT
variant, how credentials are mounted into containers, and security notes — see
the [Subscription auth guide](guides/subscription/).

## Troubleshooting

### Gateway not reachable

```
Gateway is NOT reachable on 127.0.0.1:8877.
```

Start the gateway in a separate terminal: `uvx harness-evaluator gateway --port 8877`

### No API calls found for trace_id

```
WARNING: No API calls found with trace_id=... for cell ...; cost attribution will be zero.
```

This means the harness is not routing through the gateway proxy. Common with minimal-observability harnesses (Pi, OMP) that may bypass the proxy. For partial-observability harnesses, check that the adapter's `get_env()` is setting the correct base URL.

### Docker image not found

```
docker run failed (exit 1): Unable to find image 'harness-evaluator-runner:latest' locally
```

Either pull the pre-built image or build it locally:

```bash
docker pull ghcr.io/yorch/harness-evaluator-runner:latest   # pre-built
docker build -t harness-evaluator-runner:latest .            # local build (requires clone)
```

If using the GHCR image, set `docker_image: "ghcr.io/yorch/harness-evaluator-runner:latest"` in your run config.

### No pricing found for model

```
WARNING: No pricing found for model 'my-model'; cost will be $0
```

Add the model to `DEFAULT_PRICING` in `src/harness_evaluator/gateway/models.py`. See [Configuration](configuration/#pricing-tables).

### Harness binary not found in container

The harness command fails with "command not found" inside the container. Verify the Docker image includes the harness:

```bash
docker run --rm ghcr.io/yorch/harness-evaluator-runner:latest <harness> --version
```

If missing, rebuild the image or check the Dockerfile.

### Budget cap reached

Cells are being skipped with reason "Budget cap reached". Either increase `budget_usd` in the config or reduce the number of cells (fewer harnesses, models, tasks, or repeats).
