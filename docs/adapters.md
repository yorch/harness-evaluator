---
title: Adapters
description: Adapter system, registry, per-harness details, observability tiers, and how harnesses connect to the gateway proxy.
---

# Adapters

Adapters wrap each coding harness with a uniform interface for the Docker runner. Each adapter knows how to configure, launch, and clean up its harness, and documents its observability capabilities and limitations.

## Base adapter interface

All adapters extend `BaseAdapter` (`src/harness_evaluator/adapters/base.py`):

```python
class BaseAdapter(ABC):
    def __init__(self, workdir, model, gateway_url=None, trace_id=None, config=None):
        ...

    @staticmethod
    @abstractmethod
    def info() -> AdapterInfo:
        """Return metadata about this adapter."""

    @abstractmethod
    async def prepare(self) -> None:
        """Check/install the harness (host-side, for local execution)."""

    @abstractmethod
    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        """Execute the harness non-interactively (local execution)."""

    def get_command(self, task_prompt: str) -> list[str]:
        """Return the raw command list for docker exec.
        Must use bare binary names (e.g. "claude"), not shutil.which() paths."""

    def get_env(self) -> dict[str, str]:
        """Get allowlisted env vars for the harness process."""

    async def cleanup(self) -> None:
        """Clean up after the run."""
```

### `get_command()` vs `run()`

- **`get_command()`**: Returns the CLI command as a list of strings. Used by the Docker runner to execute the harness inside a container via `docker exec`. The binary name must be bare (e.g. `"claude"`), not a `shutil.which()` resolved path — the binary lives inside the container, not on the host.

- **`run()`**: Executes the harness locally (not in Docker). Used for testing or local execution. Calls `shutil.which()` to find the binary on the host.

### `get_env()`

Returns an allowlisted set of environment variables:

```python
# Minimal allowlist (never the full host env)
allowlist = {"PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR"}

# Plus:
# - ANTHROPIC_BASE_URL or OPENAI_BASE_URL → gateway URL with trace_id
# - ANTHROPIC_API_KEY or OPENAI_API_KEY → from host environment
# - HARNESS_EVALUATOR_TRACE_ID → cell trace ID
```

The gateway URL has `?trace_id=<cell_id>` appended so the proxy can attribute calls to the correct eval cell. For OpenAI provider, `/v1` is appended to the path so the base URL ends with `/v1`.

### OAuth / subscription authentication

In addition to the default API-key auth, `get_env()` branches on the model's
`auth_mode` field to support subscription-based harness access:

#### Claude Code OAuth (`auth_mode: claude_oauth`)

Claude Code can authenticate via an OAuth token instead of an API key. In this
mode:

- `ANTHROPIC_BASE_URL` is set to the gateway proxy URL (so traffic is still
  captured).
- `ANTHROPIC_API_KEY` is **not** set.
- If the `CLAUDE_CODE_OAUTH_TOKEN` environment variable is present on the host,
  it is passed through to the container.
- The Docker runner copies `~/.claude/` (or the parent of the path in
  `credentials_path`) to a temp directory and mounts it writable into the
  container at `/workspace/.claude`, setting `CLAUDE_CONFIG_DIR` so Claude
  Code finds its credentials and can refresh expired tokens.

#### Codex ChatGPT (`auth_mode: codex_chatgpt`)

Codex can use a ChatGPT subscription instead of an OpenAI API key. In this
mode:

- `OPENAI_API_KEY` and `OPENAI_BASE_URL` are **not** set.
- The Codex adapter's `get_command()` passes `chatgpt_base_url` (with a
  `/codex` path) via the `-c` config flag instead of `openai_base_url`.
- The Docker runner copies `~/.codex/` (or the parent of the path in
  `credentials_path`) to a temp directory and mounts it writable into the
  container at `/workspace/.codex`, setting `CODEX_HOME` so Codex finds its
  credentials and can refresh expired tokens.

#### Gateway routing for the ChatGPT backend

The gateway proxy detects ChatGPT-backend requests by path and routes them to
`https://chatgpt.com/backend-api` (the `/codex/responses` path is appended
naturally) instead of `https://api.openai.com`:

| Request path | Provider | Upstream |
|--------------|----------|----------|
| `/codex/responses` | `OPENAI_CHATGPT` | `chatgpt.com/backend-api` |
| `/v1/chat/completions` | `OPENAI` | `api.openai.com` |
| `/v1/responses` | `OPENAI` | `api.openai.com` |
| `/v1/messages` | `ANTHROPIC` | `api.anthropic.com` |

The `OPENAI_CHATGPT` provider uses the same OpenAI response parser as
`OPENAI` (the ChatGPT backend returns OpenAI-format responses), so token
usage and cost are captured the same way.

### `AdapterInfo`

Each adapter provides metadata via `info()`:

```python
@dataclass
class AdapterInfo:
    name: str                    # Registry name (e.g. "claude-code")
    display_name: str            # Human-readable name
    observability_tier: str      # "full", "partial", or "minimal"
    description: str
    capabilities: list[str]      # What the harness can do
    limitations: list[str]       # What's not available
    requires_install: bool       # Whether npm install is needed
    install_instructions: str    # How to install
```

### `AdapterResult`

```python
@dataclass
class AdapterResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

## Adapter registry

The registry (`src/harness_evaluator/adapters/registry.py`) maps adapter names to classes:

```python
# Registration (at module import time)
register_adapter("claude-code", ClaudeCodeAdapter)

# Lookup (lazy-loaded on first access)
cls = get_adapter_class("claude-code")
adapter = create_adapter("claude-code", workdir, model, gateway_url, trace_id, config)

# List all
list_adapters()  # → {"claude-code": AdapterInfo(...), ...}
```

Adapters are lazy-loaded: the first call to `get_adapter_class()` or `list_adapters()` imports all adapter modules, which triggers their `register_adapter()` calls.

## Supported harnesses

| Harness | Adapter name | Observability | Provider | Install command | In default image? |
|---------|-------------|--------------|----------|-----------------|-------------------|
| OpenCode | `opencode` | `full` | Both | `npm install -g opencode-ai` | Yes |
| Aider | `aider` | `full` | Multi | `pip install aider-chat` | No |
| Claude Code | `claude-code` | `partial` | Anthropic | `npm install -g @anthropic-ai/claude-code` | Yes |
| Codex | `codex` | `partial` | OpenAI | `npm install -g @openai/codex` | Yes |
| Gemini CLI | `gemini` | `partial` | Google | `npm install -g @google/gemini-cli` | No |
| Antigravity CLI | `antigravity` | `partial` | Google | See [Antigravity CLI docs](https://antigravity.google/product/antigravity-cli) | No |
| Pi | `pi` | `minimal` | Both | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | Yes |
| OMP | `omp` | `minimal` | Both | `npm install -g @oh-my-pi/pi-coding-agent` | Yes |
| GitHub Copilot CLI | `copilot` | `minimal` | GitHub | `npm install -g @github/copilot` | No |
| Cursor CLI | `cursor` | `minimal` | Multi | Install Cursor IDE from [cursor.com](https://cursor.com/downloads) | No |
| Kiro CLI | `kiro` | `minimal` | AWS | `curl -fsSL https://cli.kiro.dev/install \| bash` | No |

The default Docker image (`ghcr.io/yorch/harness-evaluator-runner:latest`)
includes 5 harnesses (OpenCode, Claude Code, Codex, Pi, OMP). The other 6
adapters are registered in the codebase but require a custom Docker image with
the harness binary installed — see [Docker Runner](../docker-runner/) for
build instructions.

## Observability tiers

### `full` — OpenCode, Aider

Open-source harnesses. All metadata is available:
- System prompts and tool definitions are inspectable
- Context strategy is visible
- Turn-level metadata can be captured
- Sub-agent attribution available via trace ID injection
- Provider traffic captured through the gateway proxy

### `partial` — Claude Code, Codex, Gemini CLI, Antigravity CLI

Closed-source or auth-restricted harnesses that support custom API base URLs or structured output:
- System prompts and context strategy are **not visible**
- Sub-agent topology is **not exposed**
- Provider traffic **is captured** through the gateway proxy (Gemini/Antigravity may bypass if using Google auth)
- Token usage and cost are accurately attributed
- Sampling configuration is **not configurable**

### `minimal` — Pi, OMP, GitHub Copilot CLI, Cursor CLI, Kiro CLI

Closed harnesses that bypass the proxy:
- **Do not support** custom API base URLs (or use proprietary auth)
- Provider traffic **bypasses** the gateway proxy
- Only billing-level cost data may be available
- Cost accounting may rely on billing reconciliation

> **Note**: For minimal-tier harnesses, if the harness bypasses the proxy, the Docker runner will log a warning about no API calls found for the trace_id. Cost attribution will be zero unless billing API data is reconciled separately.

## Per-harness details

### OpenCode (`opencode`)

```python
# Command structure
opencode run "<task_prompt>" --model anthropic/claude-sonnet-4-20250514
```

- **Model format**: `provider/model` (e.g., `anthropic/claude-sonnet-4-20250514`)
- **Config option**: `model_flag` — override the auto-generated model flag
- **Gateway**: uses `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` env vars
- **Non-interactive**: `opencode run` subcommand

### Claude Code (`claude-code`)

```python
# Command structure
claude -p "<task_prompt>" --model claude-sonnet-4-20250514 \
    --output-format text --max-turns 50
```

- **Non-interactive**: `-p` (print) flag
- **Model**: `--model` flag
- **Max turns**: `--max-turns` (from config, default unset)
- **Output format**: `--output-format text` or `json` (from config)
- **Allowed tools**: `--allowedTools` (from config, comma-separated list)
- **Gateway**: uses `ANTHROPIC_BASE_URL` env var
- **JSON output**: when `output_format=json`, parses stdout for `num_turns` and `session_id` (ANSI escapes stripped first)
- **Provider**: Anthropic only

### Codex (`codex`)

```python
# Command structure
codex exec --model gpt-4o --sandbox workspace-write \
    -c openai_base_url=http://host.docker.internal:8877/v1?trace_id=... \
    "<task_prompt>"
```

- **Non-interactive**: `codex exec` subcommand
- **Model**: `--model` flag
- **Sandbox**: `--sandbox workspace-write` (default for evals)
- **Gateway**: passed via `-c openai_base_url=...` config override (because `OPENAI_BASE_URL` may be ignored by current Codex versions)
- **Config overrides**: `config_overrides` dict in harness config → `-c key=value` flags
- **Provider**: OpenAI only

### Pi (`pi`)

```python
# Command structure
pi -p "<task_prompt>" --model <model_flag>
```

- **Non-interactive**: `-p` (print) flag
- **Model**: `--model` flag (from config `model_flag`, optional)
- **Gateway**: env vars set, but Pi may not respect them
- **Install**: `--ignore-scripts` flag used during npm install to avoid lifecycle scripts
- **Provider**: may support both, but proxy routing is unreliable

### OMP (`omp`)

```python
# Command structure
omp -p "<task_prompt>" --model <model_flag>
```

- **Non-interactive**: `-p` (print) flag
- **Model**: `--model` flag (from config `model_flag`, optional)
- **Gateway**: env vars set, but OMP may not respect them
- **Runtime**: requires Bun (installed in Dockerfile via `curl -fsSL https://bun.sh/install | bash`)
- **Provider**: may support both, but proxy routing is unreliable

### Gemini CLI (`gemini`)

```python
# Command structure
gemini -p "<task_prompt>" --model gemini-2.5-pro --output-format json
```

- **Non-interactive**: `-p` (print) flag
- **Model**: `--model` flag
- **Output format**: `--output-format json` (default for token usage parsing) or `text`
- **Gateway**: uses `GOOGLE_GEMINI_BASE_URL` env var (with trace-aware URL)
- **API key**: `GOOGLE_API_KEY` from the model's `api_key_env`
- **Token usage**: parsed from JSON output `stats.models.<model>.tokens` structure
- **Provider**: Google only

### Aider (`aider`)

```python
# Command structure
aider --message "<task_prompt>" --model claude-sonnet-4-20250514 \
    --yes --no-auto-commits
```

- **Non-interactive**: `--message` flag (single message, then exit)
- **Model**: `--model` flag
- **Auto-confirm**: `--yes` skips all confirmation prompts (essential for evals)
- **No git commits**: `--no-auto-commits` prevents git commits during eval runs
- **Extra args**: `extra_args` config option for additional CLI flags
- **Gateway**: uses `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` env vars (multi-provider)
- **Token usage**: parsed from `Tokens: X sent, Y received` output lines
- **Provider**: multi-provider (Anthropic, OpenAI, DeepSeek, Ollama, etc.)

### GitHub Copilot CLI (`copilot`)

```python
# Command structure
copilot -p "<task_prompt>" --model claude-sonnet-4-20250514 -s --no-ask-user
```

- **Non-interactive**: `-p` (print) flag
- **Model**: `--model` flag
- **Silent mode**: `-s` suppresses interactive UI elements
- **No user prompts**: `--no-ask-user` skips confirmation prompts
- **Gateway**: **not used** — Copilot uses GitHub authentication, traffic bypasses proxy
- **Token usage**: not available (minimal tier)
- **Provider**: multi-model via GitHub Copilot subscription

### Antigravity CLI (`antigravity`)

```python
# Command structure
agy -p "<task_prompt>" --model gemini-3-pro --output-format json
```

- **Non-interactive**: `-p` (print) flag
- **Model**: `--model` flag
- **Output format**: `--output-format json` (default) or `text`
- **Gateway**: Google auth — traffic may bypass proxy
- **Token usage**: parsed from JSON output (`usage`, `metadata.usage`, or top-level fields)
- **Provider**: Google Gemini only
- **Auth**: requires prior interactive authentication (cached credentials)

### Cursor CLI (`cursor`)

```python
# Command structure
cursor agent -p "<task_prompt>" --model claude-sonnet-4-20250514
```

- **Non-interactive**: `agent` subcommand with `-p` (print) flag
- **Model**: `--model` flag
- **Mode**: `--mode` flag (agent/plan/ask; agent is default, only added if non-default)
- **Gateway**: **not used** — Cursor uses its own backend, traffic bypasses proxy
- **Token usage**: not available (minimal tier)
- **Provider**: multi-model via Cursor subscription

### Kiro CLI (`kiro`)

```python
# Command structure
kiro-cli chat --no-interactive --trust-all-tools "<task_prompt>"
```

- **Non-interactive**: `chat --no-interactive` subcommand
- **Trust tools**: `--trust-all-tools` by default; `trust_tools` config for specific tools
- **Reasoning effort**: `--effort` flag (from config)
- **Agent profile**: `--agent` flag (from config)
- **Gateway**: **not used** — Kiro uses AWS authentication, traffic bypasses proxy
- **Token usage**: not available (minimal tier)
- **Provider**: AWS-backed (formerly Amazon Q Developer CLI)

## How harnesses connect to the gateway

```
┌─────────────────────────────────────────────────────┐
│ Docker Container                                    │
│                                                     │
│  Harness CLI (e.g. claude -p "..." --model ...)     │
│      │                                              │
│      │  ANTHROPIC_BASE_URL=http://host.docker.internal:8877?trace_id=opencode__...__r0
│      │  OPENAI_BASE_URL=http://host.docker.internal:8877/v1?trace_id=codex__...__r0
│      │                                              │
│      ▼                                              │
│  HTTP request to host.docker.internal:8877          │
│  (gateway proxy on the host)                        │
└─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│ Host: Gateway Proxy (aiohttp, port 8877)            │
│  • Detects provider from API path                   │
│  • Extracts trace_id from query string              │
│  • Forwards to real provider API over HTTPS         │
│  • Parses SSE/JSON response for token usage         │
│  • Saves CapturedCall to SQLite with trace_id       │
└─────────────────────────────────────────────────────┘
```

The adapter's `get_env()` sets `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` to the gateway URL with `?trace_id=<cell_id>` appended. For OpenAI, `/v1` is appended to the path so the base URL ends with `/v1` (the proxy routes `/v1/chat/completions` and `/v1/responses`).

## Listing adapters

```bash
harness-evaluator adapters
```

Output:

```
                          Harness Adapters
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Name          ┃ Display Name         ┃ Observability ┃ Description                    ┃ Requires Install┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━═┩
│ claude-code   │ Claude Code          │ partial       │ Anthropic's terminal-based... │ Yes             │
│ codex         │ Codex (OpenAI)       │ partial       │ OpenAI's terminal-based...    │ Yes             │
│ omp           │ OMP                  │ minimal       │ OMP coding agent harness      │ Yes             │
│ opencode      │ OpenCode             │ full          │ Open-source terminal-based... │ Yes             │
│ pi            │ Pi                   │ minimal       │ Pi coding agent harness       │ Yes             │
└───────────────┴──────────────────────┴───────────────┴───────────────────────────────┴─────────────────┘
```

## Adding a new adapter

1. Create a new file in `src/harness_evaluator/adapters/` (e.g. `my_harness.py`)
2. Implement a class extending `BaseAdapter`
3. Implement `info()`, `prepare()`, `run()`, and `get_command()`
4. Call `register_adapter("my-harness", MyHarnessAdapter)` at module level
5. Add the import to `_load_all()` in `registry.py`
6. Add the harness to the Dockerfile (`npm install -g ...`)
7. Add tests in `tests/adapters/`

### Example minimal adapter

```python
from harness_evaluator.adapters.base import AdapterInfo, BaseAdapter
from harness_evaluator.adapters.registry import register_adapter

class MyHarnessAdapter(BaseAdapter):
    @staticmethod
    def info() -> AdapterInfo:
        return AdapterInfo(
            name="my-harness",
            display_name="My Harness",
            observability_tier="partial",
            description="My custom coding harness",
            capabilities=["Non-interactive mode", "Model selection"],
            limitations=["System prompt not visible"],
            requires_install=True,
            install_instructions="npm install -g my-harness",
        )

    async def prepare(self) -> None:
        # Check if binary is on PATH (for local execution)
        import shutil
        if not shutil.which("myharness"):
            raise AdapterNotInstalledError("myharness not found")

    async def run(self, task_prompt: str, timeout: int = 600) -> AdapterResult:
        # Local execution (for testing)
        env = self.get_env()
        cmd = self.get_command(task_prompt)
        return await run_command(cmd, self.workdir, env, timeout)

    def get_command(self, task_prompt: str) -> list[str]:
        return ["myharness", "--prompt", task_prompt, "--model", self.model.name]

register_adapter("my-harness", MyHarnessAdapter)
```

## Key source files

| File | Description |
|------|-------------|
| `src/harness_evaluator/adapters/base.py` | `BaseAdapter`, `AdapterInfo`, `AdapterResult`, `AdapterNotInstalledError` |
| `src/harness_evaluator/adapters/registry.py` | `register_adapter`, `get_adapter_class`, `create_adapter`, `list_adapters` |
| `src/harness_evaluator/adapters/utils.py` | `run_command()` — async subprocess execution with timeout |
| `src/harness_evaluator/adapters/claude_code.py` | Claude Code adapter |
| `src/harness_evaluator/adapters/codex.py` | Codex adapter |
| `src/harness_evaluator/adapters/opencode.py` | OpenCode adapter |
| `src/harness_evaluator/adapters/aider.py` | Aider adapter |
| `src/harness_evaluator/adapters/gemini.py` | Gemini CLI adapter |
| `src/harness_evaluator/adapters/antigravity.py` | Antigravity CLI adapter |
| `src/harness_evaluator/adapters/copilot.py` | GitHub Copilot CLI adapter |
| `src/harness_evaluator/adapters/cursor.py` | Cursor CLI adapter |
| `src/harness_evaluator/adapters/kiro.py` | Kiro CLI adapter |
| `src/harness_evaluator/adapters/pi.py` | Pi adapter |
| `src/harness_evaluator/adapters/omp.py` | OMP adapter |

## TypeScript adapter shims: assessment

All current adapters are Python CLI wrappers — each adapter's `get_command()` returns a bare binary name and argv list that the Docker runner executes inside the container via `docker exec`. The question arose whether **native TypeScript/Node.js adapter shims** are needed for harnesses that are themselves Node.js programs.

### Conclusion: Python CLI wrappers are sufficient

After reviewing all eleven adapters, **TypeScript shims are not needed**. Every supported harness exposes a CLI interface that the Python orchestrator can invoke as a subprocess. No harness requires in-process integration that would necessitate a native Node.js shim.

### Why CLI wrapping works for every harness

| Concern | How it's handled today | TS shim needed? |
|---------|------------------------|-----------------|
| **API traffic interception** | The gateway proxy captures all provider HTTP traffic via `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` env vars. This works at the HTTP layer, independent of the harness's runtime language. | No |
| **Non-interactive execution** | Every harness has a non-interactive mode: `claude -p`, `codex exec`, `opencode run`, `pi -p`, `omp -p`. These are designed for automation and scripting. | No |
| **Model selection** | All harnesses accept `--model` flags on the command line. | No |
| **Output parsing** | Adapters parse stdout/stderr (JSON or text) after the process exits. No streaming interception is required. | No |
| **Native Node.js module loading** | No harness requires loading Node.js modules in-process. The harness is a standalone binary installed via `npm install -g`. | No |
| **TypeScript-specific tooling** | Harnesses are compiled/packaged before distribution. The evaluator invokes the published binary, not TypeScript source. | No |
| **Sub-agent topology** | Not exposed by any harness regardless of language. The gateway proxy's per-call capture is the only available signal. | No |

### When TypeScript shims would become necessary

TypeScript shims would be warranted only if a future harness:

1. **Requires in-process API interception** — e.g., a harness that monkey-patches `fetch` or `http` internally and cannot be redirected via env vars. The gateway proxy would not see the traffic, so a native shim running inside the harness process would be needed to capture it. No current harness has this limitation (even Pi and OMP, which *may* bypass the proxy, do so by ignoring the env var, not by intercepting in-process).

2. **Exposes only a programmatic Node.js API** — if a harness shipped as a library (`import { run } from "some-harness"`) with no CLI entry point, a TypeScript shim would be needed to call the API and bridge results back to the Python orchestrator.

3. **Needs streaming token-level interception** — if sub-agent attribution required intercepting individual LLM calls *within* the harness process (rather than at the HTTP proxy layer), a native shim with hooks into the harness's internal call stack would be necessary.

None of these conditions apply to the current harness ecosystem. All eleven supported harnesses are distributed as CLI binaries that respect standard environment-variable configuration, making Python CLI wrappers the simplest and most maintainable integration approach.
