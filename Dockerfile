# Docker image for harness-evaluator harness execution.
#
# Contains all five supported harnesses plus a Python runtime for task
# repos that need pytest.  Built on node:26-slim (the active LTS) because Pi
# requires Node.js >= 22.19 and OMP/Codex/Claude/OpenCode all distribute as npm
# packages (installed here with Bun, which is already required by OMP).
#
# Build:
#   docker build -t harness-evaluator-runner:latest .
# Run (used by harness-evaluator's DockerRunner):
#   docker run -d --rm harness-evaluator-runner:latest sleep infinity
#   docker exec <id> claude -p "..." --model claude-sonnet-5
#
# The image is large (~2.7 GB) because it carries five harnesses, each shipping a
# native binary. For a single-harness eval you can build a trimmed variant by
# removing unused `bun install -g` lines from the harness layer below.

FROM node:26-slim AS base

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        bash \
        build-essential \
        unzip \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Symlink python3 -> python so task setup scripts that call `python` work.
RUN ln -sf /usr/bin/python3 /usr/bin/python

# ---------------------------------------------------------------------------
# Python packages for task repos
# ---------------------------------------------------------------------------
# Tasks may need pytest, pytest-asyncio, or other common packages.
# Install into the system environment so `python -m pytest` works.
# PIP_BREAK_SYSTEM_PACKAGES is set so task setup scripts that run
# `pip install -r requirements.txt` don't fail under PEP 668.
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN pip3 install --no-cache-dir \
        pytest \
        pytest-asyncio \
        pyyaml \
        requests \
        aiohttp

# ---------------------------------------------------------------------------
# Bun
# ---------------------------------------------------------------------------
# Installed before the harnesses because they are installed with `bun install -g`,
# and because OMP's CLI entry point and TypeScript task repos (`bun test`,
# `bun install`) run on it. Installed to /usr/local so it is on PATH and
# readable/executable by the non-root `harness-evaluator` user (a prior version
# installed to /root/.bun, which is 0700 and unreadable by harness-evaluator,
# breaking every TypeScript task and OMP).
ARG BUN_VERSION=1.4.5
ENV BUN_INSTALL=/usr/local
# Downloaded package tarballs are cached here. Keeping the cache out of BUN_INSTALL
# means each install layer can purge its own cache (otherwise the image carries
# ~2 GB of duplicate tarballs), and it leaves the cache in a path the non-root
# `harness-evaluator` user can write to when a task repo runs `bun install`.
ENV BUN_INSTALL_CACHE_DIR=/tmp/bun-cache
RUN curl -fsSL https://bun.sh/install | bash -s "bun-v${BUN_VERSION}" \
    && chmod -R a+rX /usr/local/bin/bun /usr/local/cache 2>/dev/null || true

# ---------------------------------------------------------------------------
# Harnesses (all installed globally via Bun)
# ---------------------------------------------------------------------------
# Versions are customizable at build time and default to a pinned, verified
# set for reproducibility. Override to evaluate a specific harness version:
#   docker build --build-arg CLAUDE_CODE_VERSION=2.0.0 -t harness-evaluator-runner:cc-2.0.0 .
# then reference that image via `docker_image:` in the run config.
ARG CLAUDE_CODE_VERSION=2.1.331
ARG CODEX_VERSION=0.172.0
ARG OPENCODE_VERSION=1.19.6
ARG PI_VERSION=1.6.0
ARG OMP_VERSION=19.1.1

# All five installs share one layer on purpose: every `bun install -g` rewrites
# entries in the global node_modules it shares with the others, so splitting them
# across RUN lines copies the whole tree into each layer and adds ~2 GB.
#
# For a single-harness eval, comment out the unused `bun install -g` lines (a
# Dockerfile comment inside a line continuation is stripped before the shell
# sees it).
RUN bun install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && bun install -g @openai/codex@${CODEX_VERSION} \
    && bun install -g opencode-ai@${OPENCODE_VERSION} \
    && bun install -g --ignore-scripts @earendil-works/pi-coding-agent@${PI_VERSION} \
    && bun install -g @oh-my-pi/pi-coding-agent@${OMP_VERSION} \
    && find /usr/local/install/global/node_modules/@oh-my-pi -type f \
        \( -name '*cuda*' \
           -o -name '*tensorrt*' \
           -o -name '*DirectML*' \
           -o -path '*darwin*' \
           -o -path '*win32*' \
           -o -name '*.dll' \
           -o -name '*.dylib' \) -delete \
    && rm -rf /tmp/bun-cache

# Record the installed harness versions as image labels so a built image is
# self-describing (and reproducible runs can be traced to exact versions).
LABEL org.opencontainers.image.title="harness-evaluator-runner" \
      io.harness-evaluator.claude-code="${CLAUDE_CODE_VERSION}" \
      io.harness-evaluator.codex="${CODEX_VERSION}" \
      io.harness-evaluator.opencode="${OPENCODE_VERSION}" \
      io.harness-evaluator.pi="${PI_VERSION}" \
      io.harness-evaluator.omp="${OMP_VERSION}" \
      io.harness-evaluator.bun="${BUN_VERSION}"

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
WORKDIR /workspace

# Create a non-root user for the harness execution. The harness-evaluator DockerRunner
# mounts the host workdir at /workspace; the harness-evaluator user needs write access.
# The Bun install tree needs no chmod here: it is created by root under the default
# 022 umask, so it is already world-readable, and a recursive chmod after the fact
# would copy every harness file into a fresh layer. The non-root verification step
# below fails the build if that ever stops being true (the old /root/.bun install was
# 0700 and broke all five harnesses).
RUN groupadd -r harness-evaluator && useradd -r -g harness-evaluator -d /workspace -s /bin/bash harness-evaluator \
    && chown -R harness-evaluator:harness-evaluator /workspace

RUN export HOME=/tmp/verify-home \
    && mkdir -p "$HOME" \
    && echo "=== Installed harnesses ===" \
    && claude --version \
    && codex --version \
    && opencode --version \
    && pi --version \
    && omp --version \
    && echo "=== Bun (TypeScript tasks + OMP) ===" \
    && bun --version \
    && echo "=== Python ===" \
    && python --version \
    && python -m pytest --version \
    && echo "=== Git ===" \
    && git --version \
    && echo "=== All harnesses ready ===" \
    && rm -rf "$HOME"

# Fail the build if the binaries are not usable by the account that actually runs
# them; root-only installs are the silent failure this guards against.
#
# Both verification steps use a throwaway HOME: harnesses write state while they
# run (codex keeps its arg0 temp dirs in ~/.codex), and state left behind by the
# build makes overlayfs fail to rename a lower-layer directory at runtime — codex
# then prints "failed to clean up stale arg0 temp dirs" on every invocation.
RUN install -d -o harness-evaluator -g harness-evaluator /tmp/verify-home \
    && su -s /bin/bash harness-evaluator -c \
        'export HOME=/tmp/verify-home \
         && claude --version && codex --version && opencode --version && pi --version \
         && omp --version && bun --version && python -m pytest --version' \
    && echo "=== Harnesses usable as harness-evaluator ===" \
    && rm -rf /tmp/verify-home

# ---------------------------------------------------------------------------
# Default command
# ---------------------------------------------------------------------------
USER harness-evaluator

# The harness-evaluator DockerRunner launches the container with `sleep infinity`
# and then uses `docker exec` for setup and harness execution.
CMD ["sleep", "infinity"]
