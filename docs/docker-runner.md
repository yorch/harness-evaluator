---
title: Docker Runner
description: Container isolation, security hardening, and how harnesses execute inside Docker containers.
---

# Docker Runner

The Docker runner (`src/heval/runner/docker.py`) executes each eval cell in an isolated Docker container. It handles the full lifecycle: repo setup, container launch, harness execution via `docker exec`, result collection, and cleanup.

## Container image

The runner image contains all five harnesses, Node.js 22, Python 3, Git, and Bun. You can either pull the pre-built image from GHCR or build it locally.

### Pull the pre-built image (recommended)

A pre-built image is published to the GitHub Container Registry on every push to `main`:

```bash
docker pull ghcr.io/yorch/harnessbench-runner:latest
```

Available tags: `latest`, `sha-<short-hash>` (pinned to a commit), and `main`.

Reference it in your run config:

```yaml
docker_image: "ghcr.io/yorch/harnessbench-runner:latest"
```

### Build locally

```bash
docker build -t harnessbench-runner:latest .
```

Harness (and Bun) versions are build args, so you can pin a specific harness
release to compare versions:

```bash
docker build --build-arg CLAUDE_CODE_VERSION=2.0.0 -t harnessbench-runner:cc-2.0.0 .
```

Build args: `CLAUDE_CODE_VERSION`, `CODEX_VERSION`, `OPENCODE_VERSION`,
`PI_VERSION`, `OMP_VERSION`, `BUN_VERSION` — each defaults to a pinned,
verified version. The installed versions are recorded as `io.heval.*` image
labels. See [Configuration](configuration/#docker-image-configuration).

### Image contents

| Component | Purpose |
|-----------|---------|
| Node.js 22 | Required by Pi (≥22.19) and all npm-distributed harnesses |
| Python 3 + pip | For task repos that need pytest |
| Git | Repo cloning and diff evaluation |
| Claude Code (`claude`) | Anthropic's CLI harness |
| Codex (`codex`) | OpenAI's CLI harness |
| OpenCode (`opencode`) | Open-source agentic coding tool |
| Pi (`pi`) | Minimal terminal coding harness |
| OMP (`omp`) | Coding-first fork of Pi with Rust core |
| Bun | Runtime required by OMP's CLI entry point |
| pytest, pyyaml, requests, aiohttp | Python packages for task repos |

The image is ~1.2 GB because it carries all five harnesses. For single-harness evals, you can build a trimmed variant by commenting out unused `RUN` lines in the Dockerfile.

### Non-root user

The Dockerfile creates a `heval` user:

```dockerfile
RUN groupadd -r heval && useradd -r -g heval -d /workspace -s /bin/bash heval \
    && chown -R heval:heval /workspace
USER heval
```

Harnesses run as this non-root user inside the container.

### Default command

The container runs `sleep <timeout+30>` so the runner can `docker exec` into it for setup and harness execution. The container is stopped after the harness completes.

## Container lifecycle

```
1. Host: Create workdir, clone/copy task repo
   │
2. Host: Delete prior gateway calls for this trace_id
   │  (prevents double-counting on re-runs)
   │
3. Host: docker run -d --rm --cap-drop=ALL ...
   │  Launch detached container with:
   │  • workdir mounted at /workspace
   │  • allowlisted env vars (--env, not full host env)
   │  • --add-host host.docker.internal:host-gateway
   │  • --stop-timeout <task_timeout>
   │  • sleep <timeout+30> as the command
   │
4. Host: docker exec -w /workspace/repo <container> bash /workspace/setup.sh
   │  Run setup script if present (e.g. pip install -r requirements.txt)
   │
5. Host: docker exec -w /workspace/repo <container> <harness command>
   │  Execute the harness CLI (from adapter.get_command())
   │  Timeout enforced via asyncio.wait_for
   │
6. Host: docker stop <container_id>
   │  Stop and remove the container (--rm handles removal)
   │
7. Host: git add -A && git commit
   │  Stage and commit harness changes for diff evaluation
   │
8. Host: Evaluate results (SWE tests or open-ended judge)
   │
9. Host: Collect token usage from gateway (by trace_id)
```

### Why `docker exec` instead of `docker run` per command

The runner uses a long-running container (`sleep <timeout+30>`) and `docker exec` for setup and harness execution. This allows:

- Running setup scripts before the harness
- Multiple exec commands in the same container
- Clean separation of setup and execution phases
- The container's filesystem state persists between exec calls

## Security hardening

### `--cap-drop=ALL`

All Linux capabilities are dropped. The harness only needs file I/O and network access to the gateway/provider — it does not need `SYS_PTRACE`, `NET_ADMIN`, `MKNOD`, or other privileged operations.

### Environment variable allowlist

The adapter's `get_env()` method passes only a minimal set of env vars to the container:

```python
allowlist = {"PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR"}
```

Plus:
- `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` → gateway proxy URL with trace_id
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` → from the host environment
- `HEVAL_TRACE_ID` → the cell's trace ID

The full host environment is never passed through. This prevents leaking host secrets (SSH keys, cloud credentials, etc.) into the container.

### Container name sanitization

Cell IDs are sanitized for use as Docker container names (Docker requires `[a-zA-Z0-9][a-zA-Z0-9_.-]*`):

```python
def _sanitize_container_name(cell_id: str) -> str:
    name = _SAFE_NAME_RE.sub("-", cell_id)  # Replace unsafe chars with -
    if name and not name[0].isalnum():
        name = "heval-" + name
    return f"heval-{name}"
```

### Network access

Containers reach the gateway proxy via `host.docker.internal`:

```
--add-host host.docker.internal:host-gateway
```

For environments where `host.docker.internal` doesn't work, the runner supports `--network=host` as a fallback (`use_host_network=True`).

## Repo setup

The runner supports three repo types:

| Type | Example | Method |
|------|---------|--------|
| Remote URL | `https://github.com/org/repo` | `git clone` + optional `git checkout <commit>` |
| Local git repo | `tasks/repos/my-task` (has `.git`) | `git clone` (preserves history) |
| Plain directory | `tasks/repos/swe-bugfix-001` (no `.git`) | `shutil.copytree` + `git init` + initial commit |

> **Note**: Task repos in `tasks/repos/` are plain directories (no `.git`). The runner copies them via `shutil.copytree` and inits a fresh git repo. Do not assume `repo_commit` hashes in task YAMLs are valid for these repos.

### Relative path resolution

`_clone_repo` resolves relative `repo_url` paths against the project root (`Path(__file__).resolve().parents[3]`), not the current working directory. This means `repo_url: tasks/repos/swe-bugfix-001` works regardless of where `harnessbench run` is invoked.

### Setup scripts

If a task defines `setup_script`, it is written to `/workspace/setup.sh` in the container and executed via `docker exec` with the repo directory as the working directory:

```bash
docker exec -w /workspace/repo <container> bash /workspace/setup.sh
```

This ensures relative paths (e.g., `requirements.txt`) resolve correctly.

## Timeout enforcement

The harness command timeout comes from `task.timeout_seconds` (default 600s). The timeout is enforced via `asyncio.wait_for` on the `docker exec` subprocess.

If the harness times out:
1. The subprocess is killed
2. An `AdapterResult` with `timed_out=True` is returned
3. The Docker runner raises `RetryableError`, which the orchestrator retries with exponential backoff

The container's `--stop-timeout` is set to the same value, ensuring Docker kills the container promptly on stop.

## Post-execution: git commit

After the harness completes, the runner stages and commits all changes on the host:

```python
git config user.email "heval@local"
git config user.name "heval"
git add -A
git commit -m "harness output"
```

This ensures `git diff` works for evaluation. If no changes were made, the commit fails silently (which is fine — the evaluator handles the no-change case).

Local git identity is used (not `--global`) so the host's git config is not affected. This is required because containers/CI may not have a git identity configured.

## Token usage collection

After harness execution and evaluation, the runner collects token usage from the gateway database:

```python
store = CallStore(str(gateway_db_path))
calls = store.get_by_trace(cell.cell_id)
for call in calls:
    usage.input_tokens += call.usage.input_tokens
    usage.output_tokens += call.usage.output_tokens
    # ... cache_read, cache_write, reasoning
    total_cost += call.cost.total
    num_api_calls += 1
```

If no calls are found for the trace_id, a warning is logged — this usually indicates trace ID propagation is not working (common with minimal-observability harnesses that bypass the proxy).

## Resource limits

The runner supports optional resource limits:

| Parameter | Docker flag | Description |
|-----------|------------|-------------|
| `memory_limit` | `--memory` | Container memory limit (e.g., `"2g"`) |
| `cpu_limit` | `--cpus` | CPU limit (e.g., `"2.0"`) |

These are not set by default. Configure them when running parallel evals to prevent resource contention.
