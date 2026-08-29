# Docker image for heval harness execution.
#
# Contains all five supported harnesses plus a Python runtime for task
# repos that need pytest.  Built on node:22-slim because Pi requires
# Node.js >= 22.19 and OMP/Codex/Claude/OpenCode all distribute via npm.
#
# Build:
#   docker build -t heval-runner:latest .
# Run (used by heval's DockerRunner):
#   docker run -d --rm heval-runner:latest sleep infinity
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
# Versions are pinned for reproducibility. Bump deliberately and re-verify.
# Claude Code — Anthropic's CLI
RUN npm install -g @anthropic-ai/claude-code@2.1.251

# Codex — OpenAI's CLI
RUN npm install -g @openai/codex@0.151.0

# OpenCode — open-source agentic coding tool
RUN npm install -g opencode-ai@1.18.25

# Pi — minimal terminal coding harness
# --ignore-scripts avoids running lifecycle scripts during install.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.4

# Bun runtime — required by OMP's CLI entry point AND used to run
# TypeScript task repos (`bun test`, `bun install`). Installed to
# /usr/local so it is on PATH and readable/executable by the non-root
# `heval` user (a prior version installed to /root/.bun, which is 0700
# and unreadable by heval, breaking every TypeScript task and OMP).
ENV BUN_INSTALL=/usr/local
RUN curl -fsSL https://bun.sh/install | bash -s "bun-v1.4.0" \
    && chmod -R a+rX /usr/local/bin/bun /usr/local/cache 2>/dev/null || true

# OMP — coding-first fork of Pi with Rust core
RUN npm install -g @oh-my-pi/pi-coding-agent@18.0.11

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

# Create a non-root user for the harness execution. The heval DockerRunner
# mounts the host workdir at /workspace; the heval user needs write access.
RUN groupadd -r heval && useradd -r -g heval -d /workspace -s /bin/bash heval \
    && chown -R heval:heval /workspace

USER heval

# The heval DockerRunner launches the container with `sleep infinity`
# and then uses `docker exec` for setup and harness execution.
CMD ["sleep", "infinity"]
