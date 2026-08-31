---
title: Subscription Auth
description: Run harness-evaluator with a Claude Code (OAuth) or Codex (ChatGPT) subscription instead of pay-per-token API keys.
---

# Subscription Auth

By default, harness-evaluator authenticates to provider APIs with pay-per-token
API keys. Both Claude Code and Codex also support **subscription-based access** —
Claude Code via OAuth (Claude Pro/Max) and Codex via a ChatGPT subscription. This
guide walks through obtaining the credential files, wiring them into a run config,
and understanding how cost accounting changes.

## When to use subscription auth

- You have a Claude Pro/Max or ChatGPT subscription and want to evaluate the
  harnesses as your subscription sees them (rate limits, model access, etc.).
- You want to avoid per-token API charges during evaluation.
- You want to measure **token efficiency** (tokens consumed) without the runs
  counting against a dollar budget.

Subscription runs still capture full token usage through the gateway proxy —
only the **cost** accounting changes (see [Cost mode](#cost-mode) below).

## The three auth modes

| Mode | `auth_mode` value | Harness | Credential source |
|------|-------------------|---------|-------------------|
| API key | `api_key` (default) | All | Env var named in `api_key_env` |
| Claude Code OAuth | `claude_oauth` | Claude Code | `~/.claude/.credentials.json` |
| Codex ChatGPT | `codex_chatgpt` | Codex | `~/.codex/auth.json` |

See [Configuration → Authentication modes](../configuration/#authentication-modes)
for the full field reference.

## Prerequisites

- A working **Claude Code** or **Codex** CLI installation on the host (used only
  to perform the one-time login — the eval itself runs inside Docker).
- An active Claude Pro/Max subscription (for `claude_oauth`) or ChatGPT
  subscription (for `codex_chatgpt`).
- The harness-evaluator Docker runner image (see
  [Getting Started](../getting-started/#step-3-pull-the-docker-image)).

## Step 1: Obtain OAuth credentials

The credential files are created by logging in to the harness CLI on your host
machine. harness-evaluator never performs the login for you — it only mounts the
resulting credentials into the eval container.

### Claude Code (OAuth)

Run Claude Code interactively and complete the OAuth login:

```bash
claude
# Follow the prompt to sign in with your Anthropic account (Claude Pro/Max).
# This writes the OAuth credential file to ~/.claude/.credentials.json
```

Verify the credential file exists:

```bash
ls -l ~/.claude/.credentials.json
```

### Codex (ChatGPT subscription)

Run the Codex login flow and choose ChatGPT auth:

```bash
codex login
# Select the ChatGPT account option and complete the browser login.
# This writes the credential file under ~/.codex/ (e.g. ~/.codex/auth.json)
```

Verify the credential directory exists:

```bash
ls -l ~/.codex/
```

> **Note**: The exact filename Codex writes may vary by version. harness-evaluator
> mounts the **parent directory** of whatever path you put in `credentials_path`,
> so point it at the credential file and the whole `~/.codex/` directory is copied
> into the container.

## Step 2: Write the run config

### Claude Code with OAuth

```yaml
name: "claude-subscription-run"
description: "Claude Code on a Claude Pro subscription (OAuth)"

harnesses:
  - name: claude-code
    adapter: claude-code
    observability_tier: partial
    config:
      max_turns: 50

models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY   # unused under claude_oauth, but required by the schema
    auth_mode: claude_oauth
    credentials_path: "~/.claude/.credentials.json"
    cost_mode: subscription

tasks:
  - swe-bugfix-001
  - open-design-001

repeats: 3
budget_usd: null   # no dollar cap — subscription is flat-rate
gateway_port: 8877
```

With `claude_oauth`:
- `ANTHROPIC_BASE_URL` is set to the gateway proxy URL (so traffic is still
  captured for token accounting).
- `ANTHROPIC_API_KEY` is **not** set — the harness authenticates with its OAuth
  token instead.
- If the `CLAUDE_CODE_OAUTH_TOKEN` environment variable is present on the host,
  it is passed through to the container.
- The Docker runner copies `~/.claude/` (the parent of `credentials_path`) to a
  temp directory and mounts it writable into the container at `/workspace/.claude`,
  setting `CLAUDE_CONFIG_DIR` so Claude Code finds its credentials and can
  refresh expired access tokens.

### Codex with a ChatGPT subscription

```yaml
name: "codex-subscription-run"
description: "Codex on a ChatGPT subscription"

harnesses:
  - name: codex
    adapter: codex
    observability_tier: partial
    config: {}

models:
  - name: gpt-5
    provider: openai
    api_key_env: OPENAI_API_KEY   # unused under codex_chatgpt, but required by the schema
    auth_mode: codex_chatgpt
    credentials_path: "~/.codex/auth.json"
    cost_mode: subscription

tasks:
  - swe-bugfix-001
  - open-design-001

repeats: 3
budget_usd: null
gateway_port: 8877
```

With `codex_chatgpt`:
- `OPENAI_API_KEY` and `OPENAI_BASE_URL` are **not** set.
- The Codex adapter passes `chatgpt_base_url` (with a `/codex` path) via the `-c`
  config flag instead of `openai_base_url`, so traffic routes through the gateway
  proxy to the ChatGPT backend.
- The Docker runner copies `~/.codex/` (the parent of `credentials_path`) to a
  temp directory and mounts it writable into the container at `/workspace/.codex`,
  setting `CODEX_HOME` so Codex finds its credentials and can refresh expired
  access tokens.

## Step 3: Run the eval

The flow is identical to an API-key run — start the gateway, then run:

```bash
# Terminal 1: start the gateway proxy (still required for token accounting)
harness-evaluator gateway --port 8877

# Terminal 2: dry-run to preview the matrix
harness-evaluator run claude-subscription.yaml --dry-run

# Execute
harness-evaluator run claude-subscription.yaml
```

Token usage, latency, and API call counts are captured exactly as with API-key
auth. Only the cost figures differ (see below).

## Cost mode

The `cost_mode` field controls how cost is accounted:

| `cost_mode` | Cost recorded | Counts against `budget_usd`? | Use when |
|-------------|---------------|------------------------------|----------|
| `platform` (default) | Priced per token from the pricing table | Yes | Pay-per-token API key |
| `subscription` | `$0` per call | No | Flat-rate subscription |

With `subscription`, token usage is still captured and stored (so you can analyze
token efficiency), but `total_cost` is recorded as `$0` and the tokens do not
deplete the `budget_usd` cap. This is the correct mode for Claude Pro/Max and
ChatGPT subscriptions, where you are not billed per token.

You can mix modes in a single run — e.g. compare a subscription-backed Claude Code
against an API-key-backed Codex:

```yaml
models:
  - name: claude-sonnet-4-20250514
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    auth_mode: claude_oauth
    credentials_path: "~/.claude/.credentials.json"
    cost_mode: subscription
  - name: gpt-4o
    provider: openai
    api_key_env: OPENAI_API_KEY
    auth_mode: api_key        # default
    cost_mode: platform       # default
```

## How credentials are mounted

The Docker runner never mounts your real credential directory directly. For
safety it:

1. Copies the **parent directory** of `credentials_path` to a fresh temp
   directory on the host.
2. Mounts that temp copy (writable) into the container so the harness can refresh
   expired access tokens.
3. Excludes the credential mount point (`.claude` / `.codex`) from the git commit
   diff, so tokens never appear in evaluation diffs.

If `credentials_path` does not exist, the runner logs a warning and skips the
mount — the harness will then fail to authenticate.

## Security considerations

OAuth credential files contain **refresh tokens** that grant ongoing access to
your account. Treat them with the same care as API keys:

- Store credential files with restrictive permissions (`chmod 600`).
- Never commit credential files to a repository.
- The mounted copy is writable (so token refresh works), which means the harness
  process inside the container can read the refresh token. Task YAMLs are trusted
  input (see [Configuration → Task trust model](../configuration/#task-trust-model)),
  but be aware that a malicious task could exfiltrate OAuth tokens over the
  network — the same risk as with API keys, since the container has network
  access to the gateway.

## Troubleshooting

### "No API calls found for trace_id"

The harness is not routing through the gateway proxy. For `codex_chatgpt`,
confirm the adapter is passing `chatgpt_base_url` via `-c` (check the adapter
config). For `claude_oauth`, confirm `ANTHROPIC_BASE_URL` is being set.

### Authentication failures inside the container

The credential file was not found or has expired. Verify `credentials_path`
points to a real file on the host and that the OAuth session is still valid
(re-run the interactive login if the refresh token has been revoked).

### Token refresh not working

The credential mount must be writable (it is, by default). If you have customized
the Docker runner, ensure the `credential_mounts` are passed through with
writable permissions.

## See also

- [Configuration → Authentication modes](../configuration/#authentication-modes) — full field reference
- [Adapters → OAuth / subscription authentication](../adapters/#oauth--subscription-authentication) — adapter-level behavior
- [Gateway Proxy → Provider detection](../gateway-proxy/#1-provider-detection) — how `/codex/responses` is routed
