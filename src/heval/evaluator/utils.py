"""Shared helpers for the SWE and open-ended evaluators."""

from __future__ import annotations

import subprocess
from pathlib import Path


def get_workdir_diff(workdir: Path) -> str:
    """Return the git diff of changes made by a harness in ``workdir``.

    Tries multiple strategies so it works whether the harness committed,
    staged, or merely modified/created files:

    1. ``git diff HEAD`` — uncommitted changes (staged + unstaged)
    2. ``git diff HEAD~1`` — changes in the last commit
    3. Untracked files via ``git status --porcelain`` + ``git diff --no-index``

    Security: untracked entries that are symlinks are skipped. Otherwise a
    harness could create ``repo/leak -> /etc/passwd`` and have the host-side
    diff read arbitrary host files into the results database.
    """
    try:
        # Uncommitted changes (staged + unstaged) against HEAD.
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=workdir,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        if result.stdout.strip():
            return result.stdout

        # Fall back to the last commit's changes.
        result = subprocess.run(
            ["git", "diff", "HEAD~1"],
            cwd=workdir,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        if result.stdout.strip():
            return result.stdout

        # Untracked files: generate real content diffs.
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
        if not status_result.stdout.strip():
            return ""

        diffs: list[str] = []
        for line in status_result.stdout.strip().splitlines():
            if not line.strip():
                continue
            # Porcelain format: "XY <path>" (2-char status + space + path).
            if line[:2] != "??":
                continue
            file_path = line[3:]
            full_path = workdir / file_path
            # Skip symlinks to avoid disclosing host files outside the workdir.
            if full_path.is_symlink() or not full_path.is_file():
                continue
            diff_result = subprocess.run(
                ["git", "diff", "--no-index", "/dev/null", str(full_path)],
                cwd=workdir,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
            # --no-index exits 1 when files differ (expected).
            if diff_result.stdout.strip():
                diffs.append(diff_result.stdout)

        if diffs:
            return "\n".join(diffs)

        # No untracked files with content diffs; fall back to the status text.
        return f"--- untracked changes ---\n{status_result.stdout}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
