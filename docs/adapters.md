---
title: Adapters
description: Adapter system, registry, per-harness details, observability tiers, and how harnesses connect to the gateway proxy.
---

# Adapters

Adapters wrap each coding harness with a uniform interface for the Docker runner. Each adapter knows how to configure, launch, and clean up its harness, and documents its observability capabilities and limitations.

## Base adapter interface

All adapters extend `BaseAdapter` (`src/heval/adapters/base.py`):

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
# - HEVAL_TRACE_ID → cell trace ID
```

The gateway URL has `?trace_id=<cell_id>` appended so the proxy can attribute calls to the correct eval cell. For OpenAI provider, `/v1` is appended to the path so the base URL ends with `/v1`.

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

The registry (`src/heval/adapters/registry.py`) maps adapter names to classes:

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

| Harness | Adapter name | Observability | Provider | Install command |
|---------|-------------|--------------|----------|-----------------|
| OpenCode | `opencode` | `full` | Both | `npm install -g opencode-ai` |
| Claude Code | `claude-code` | `partial` | Anthropic | `npm install -g @anthropic-ai/claude-code` |
| Codex | `codex` | `partial` | OpenAI | `npm install -g @openai/codex` |
| Pi | `pi` | `minimal` | Both | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` |
| OMP | `omp` | `minimal` | Both | `npm install -g @oh-my-pi/pi-coding-agent` |

## Observability tiers

### `full` — OpenCode

Open-source harness. All metadata is available:
- System prompts and tool definitions are inspectable
- Context strategy is visible
- Turn-level metadata can be captured
- Sub-agent attribution available via trace ID injection
- Provider traffic captured through the gateway proxy

### `partial` — Claude Code, Codex

Closed-source harnesses that respect custom API base URLs:
- System prompts and context strategy are **not visible**
- Sub-agent topology is **not exposed**
- Provider traffic **is captured** through the gateway proxy
- Token usage and cost are accurately attributed
- Sampling configuration is **not configurable**

### `minimal` — Pi, OMP

Closed harnesses that may bypass the proxy:
- May **not support** custom API base URLs
- Provider traffic **may bypass** the gateway proxy
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
harnessbench adapters
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

1. Create a new file in `src/heval/adapters/` (e.g. `my_harness.py`)
2. Implement a class extending `BaseAdapter`
3. Implement `info()`, `prepare()`, `run()`, and `get_command()`
4. Call `register_adapter("my-harness", MyHarnessAdapter)` at module level
5. Add the import to `_load_all()` in `registry.py`
6. Add the harness to the Dockerfile (`npm install -g ...`)
7. Add tests in `tests/adapters/`

### Example minimal adapter

```python
from heval.adapters.base import AdapterInfo, BaseAdapter
from heval.adapters.registry import register_adapter

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
| `src/heval/adapters/base.py` | `BaseAdapter`, `AdapterInfo`, `AdapterResult`, `AdapterNotInstalledError` |
| `src/heval/adapters/registry.py` | `register_adapter`, `get_adapter_class`, `create_adapter`, `list_adapters` |
| `src/heval/adapters/utils.py` | `run_command()` — async subprocess execution with timeout |
| `src/heval/adapters/claude_code.py` | Claude Code adapter |
| `src/heval/adapters/codex.py` | Codex adapter |
| `src/heval/adapters/opencode.py` | OpenCode adapter |
| `src/heval/adapters/pi.py` | Pi adapter |
| `src/heval/adapters/omp.py` | OMP adapter |
