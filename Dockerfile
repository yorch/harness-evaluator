# Docker image for harness-evaluator harness execution.
#
# Contains all five supported harnesses plus a Python runtime for task
# repos that need pytest.  Built on node:22-slim because Pi requires
# Node.js >= 22.19 and OMP/Codex/Claude/OpenCode all distribute via npm.
#
# Build:
#   docker build -t harness-evaluator-runner:latest .
# Run (used by harness-evaluator's DockerRunner):
#   docker run -d --rm harness-evaluator-runner:latest sleep infinity
#   docker exec <id> claude -p "..." --model claude-sonnet-4-20250514
#
# The image is intentionally large (~1.2 GB) because it carries five
# harnesses.  For a single-harness eval you can build a trimmed variant
# by commenting out unused RUN lines.

FROM node:22-slim AS base

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
RUN pip3 install --no-cache-dir --break-system-packages \
        pytest \
        pytest-asyncio \
        pyyaml \
        requests \
        aiohttp

# ---------------------------------------------------------------------------
# Harnesses (all installed globally via npm)
# ---------------------------------------------------------------------------
# Versions are customizable at build time and default to a pinned, verified
# set for reproducibility. Override to evaluate a specific harness version:
#   docker build --build-arg CLAUDE_CODE_VERSION=2.0.0 -t harness-evaluator-runner:cc-2.0.0 .
# then reference that image via `docker_image:` in the run config.
ARG CLAUDE_CODE_VERSION=2.1.251
ARG CODEX_VERSION=0.151.0
ARG OPENCODE_VERSION=1.18.25
ARG PI_VERSION=0.84.4
ARG OMP_VERSION=18.0.11
ARG BUN_VERSION=1.4.0

# Claude Code — Anthropic's CLI
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Codex — OpenAI's CLI
RUN npm install -g @openai/codex@${CODEX_VERSION}

# OpenCode — open-source agentic coding tool
RUN npm install -g opencode-ai@${OPENCODE_VERSION}

# Pi — minimal terminal coding harness
# --ignore-scripts avoids running lifecycle scripts during install.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@${PI_VERSION}

# Bun runtime — required by OMP's CLI entry point AND used to run
# TypeScript task repos (`bun test`, `bun install`). Installed to
# /usr/local so it is on PATH and readable/executable by the non-root
# `harness-evaluator` user (a prior version installed to /root/.bun, which is 0700
# and unreadable by harness_evaluator, breaking every TypeScript task and OMP).
ENV BUN_INSTALL=/usr/local
RUN curl -fsSL https://bun.sh/install | bash -s "bun-v${BUN_VERSION}" \
    && chmod -R a+rX /usr/local/bin/bun /usr/local/cache 2>/dev/null || true

# OMP — coding-first fork of Pi with Rust core
RUN npm install -g @oh-my-pi/pi-coding-agent@${OMP_VERSION}

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
RUN echo "=== Installed harnesses ===" \
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
    && echo "=== All harnesses ready ==="

# ---------------------------------------------------------------------------
# Working directory and default command
# ---------------------------------------------------------------------------
WORKDIR /workspace

# Create a non-root user for the harness execution. The harness-evaluator DockerRunner
# mounts the host workdir at /workspace; the harness-evaluator user needs write access.
RUN groupadd -r harness-evaluator && useradd -r -g harness-evaluator -d /workspace -s /bin/bash harness-evaluator \
    && chown -R harness-evaluator:harness-evaluator /workspace

USER harness-evaluator

# The harness-evaluator DockerRunner launches the container with `sleep infinity`
# and then uses `docker exec` for setup and harness execution.
CMD ["sleep", "infinity"]
