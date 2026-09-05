#!/usr/bin/env python3
"""Check the docs against the code they describe.

Three classes of drift, all of which have actually shipped in this repo and all
of which are mechanically detectable:

1. **CLI coverage** -- every command and option the CLI exposes has an entry in
   ``docs/cli-reference.md``, and the reference documents nothing that no longer
   exists. ``check-keys`` shipped undocumented; ``calibrate --calibration-file``
   was missing from an otherwise complete options table.
2. **Link integrity** -- every relative link and anchor between docs resolves,
   in both the GitHub view (``foo.md#bar``) and the rendered Starlight site
   (``foo/#bar``).
3. **Navigation coverage** -- every page under ``docs/`` is reachable from both
   hand-maintained navigation surfaces: the table of contents in
   ``docs/index.md`` and the sidebar in ``site/astro.config.mjs``. Nothing
   otherwise stops a new page from being invisible.

Run with no arguments from the repository root. Exits non-zero on drift, listing
each problem. Standard library only, so CI needs no extra install step.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
CLI_REFERENCE = DOCS / "cli-reference.md"
SITE_CONFIG = REPO / "site" / "astro.config.mjs"

# Options every Typer command inherits; they are not worth a table row each.
_UNIVERSAL_OPTIONS = {"--help", "--install-completion", "--show-completion"}

_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_OPTION = re.compile(r"(--[a-z][a-z0-9-]*)")
# A row of a Markdown options table: "| `--flag` / `--no-flag` | flag | ... |".
_TABLE_ROW = re.compile(r"^\s*\|\s*`--[^|]*\|.*$", re.M)


def _strip_code_fences(text: str) -> str:
    """Blank out fenced code blocks, preserving line numbering.

    Shell samples in these docs are full of ``# comment`` lines, which the
    heading pattern would otherwise read as headings: 69 phantom anchors across
    ``docs/``, every one of which could mask a genuinely dead ``#anchor`` link.
    Links inside samples are not real links either, so both are read from the
    stripped text.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith(("```", "~~~")):
                fence = stripped[:3]
                out.append("")
                continue
        # Only the matching marker closes the block, so a ``` inside a ~~~
        # sample does not end it early.
        elif stripped.startswith(fence):
            fence = None
            out.append("")
            continue
        out.append("" if fence else line)
    return "\n".join(out)


def _slug(heading: str) -> str:
    """Convert a Markdown heading to its GitHub/Starlight anchor.

    Underscores survive and each space becomes one hyphen, so ``cost_mode``
    anchors as ``#cost_mode`` rather than ``#costmode``.
    """
    text = re.sub(r"[`*]", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text).strip("-")


def _doc_pages() -> list[Path]:
    return sorted(DOCS.rglob("*.md"))


def _page_key(path: Path) -> str:
    return str(path.relative_to(DOCS)).removesuffix(".md")


def _run_cli(args: list[str]) -> str:
    """Return ``--help`` output for a CLI invocation, or "" if unavailable."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "harness_evaluator.cli", *args, "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            env={"COLUMNS": "200", "PATH": "/usr/bin:/bin", "NO_COLOR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout


def check_cli_coverage() -> list[str]:
    """Every CLI command and option appears in the reference, and vice versa."""
    problems: list[str] = []
    root_help = _run_cli([])
    if not root_help:
        return ["could not run the CLI to enumerate commands"]

    commands = sorted(set(re.findall(r"^\s*│\s+([a-z][a-z-]*)\s{2,}", root_help, re.M)))
    if not commands:
        return ["could not parse any commands from the CLI help output"]

    reference = CLI_REFERENCE.read_text(encoding="utf-8")
    documented = set(re.findall(r"^## harness-evaluator ([a-z-]+)\s*$", reference, re.M))

    for command in commands:
        if command not in documented:
            problems.append(
                f"{CLI_REFERENCE.name}: command '{command}' exists but has no "
                f"'## harness-evaluator {command}' section"
            )
    for command in sorted(documented - set(commands)):
        problems.append(
            f"{CLI_REFERENCE.name}: documents '{command}', which the CLI no longer exposes"
        )

    # Options are compared per command section so a flag documented under the
    # wrong command still counts as missing.
    sections = re.split(r"^## harness-evaluator ([a-z-]+)\s*$", reference, flags=re.M)
    by_command = {sections[i]: sections[i + 1] for i in range(1, len(sections), 2)}
    for command in commands:
        section = by_command.get(command)
        if section is None:
            continue
        actual = set(_OPTION.findall(_run_cli([command]))) - _UNIVERSAL_OPTIONS
        # Only the options table counts. Prose in a section routinely mentions
        # other commands' flags ("see `harness-evaluator dashboard --db`"), and
        # treating those as documented-here produced false "no longer exists"
        # reports for flags that were never this command's to begin with.
        documented_opts = {
            opt
            for row in _TABLE_ROW.findall(section)
            for opt in _OPTION.findall(row)
        } - _UNIVERSAL_OPTIONS
        for opt in sorted(actual - documented_opts):
            problems.append(f"{CLI_REFERENCE.name}: '{command}' option {opt} is undocumented")
        for opt in sorted(documented_opts - actual):
            problems.append(
                f"{CLI_REFERENCE.name}: '{command}' documents {opt}, which no longer exists"
            )
    return problems


def check_links() -> list[str]:
    """Relative links and anchors between docs resolve."""
    problems: list[str] = []
    pages = _doc_pages()
    keys = {_page_key(p) for p in pages}
    anchors = {
        _page_key(p): {
            _slug(h)
            for h in _HEADING.findall(
                _strip_code_fences(p.read_text(encoding="utf-8"))
            )
        }
        for p in pages
    }

    for page in pages:
        key = _page_key(page)
        body = _strip_code_fences(page.read_text(encoding="utf-8"))
        for text, href in _LINK.findall(body):
            if href.startswith(("http://", "https://", "mailto:", "#!")):
                continue
            target, _, fragment = href.partition("#")

            if not target:  # same-page anchor
                if fragment and fragment not in anchors[key]:
                    problems.append(f"{key}.md: dead anchor '#{fragment}' [{text}]")
                continue

            if target.endswith(".md"):  # GitHub-style link
                resolved = (page.parent / target).resolve()
                if not resolved.is_file():
                    problems.append(f"{key}.md: broken link '{target}' [{text}]")
                    continue
                hit = _page_key(resolved)
            else:  # Starlight-style link, e.g. "orchestrator/" or "../adapters/"
                candidate = (Path(key).parent / target.rstrip("/")).as_posix()
                candidate = Path(candidate).resolve().relative_to(Path.cwd()).as_posix() \
                    if candidate.startswith("/") else _normalize(candidate)
                hit = candidate if candidate in keys else target.strip("/")
                if hit not in keys:
                    problems.append(f"{key}.md: link to unknown page '{target}' [{text}]")
                    continue

            if fragment and fragment not in anchors.get(hit, set()):
                problems.append(f"{key}.md: dead anchor '{target}#{fragment}' [{text}]")
    return problems


def _normalize(relative: str) -> str:
    """Collapse ``..`` segments in a doc-relative path."""
    parts: list[str] = []
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def check_navigation() -> list[str]:
    """Every doc page is reachable from both hand-maintained nav surfaces."""
    problems: list[str] = []
    pages = [_page_key(p) for p in _doc_pages() if _page_key(p) != "index"]

    index = (DOCS / "index.md").read_text(encoding="utf-8")
    for key in pages:
        if key not in index:
            problems.append(f"docs/index.md: no table-of-contents entry for '{key}'")

    if SITE_CONFIG.is_file():
        sidebar = SITE_CONFIG.read_text(encoding="utf-8")
        listed = set(re.findall(r"slug:\s*'docs/([^']+)'", sidebar))
        for key in pages:
            if key not in listed:
                problems.append(
                    f"site/astro.config.mjs: '{key}' is missing from the sidebar, "
                    f"so the page would not be navigable on the site"
                )
    return problems


def main() -> int:
    checks = (
        ("CLI reference coverage", check_cli_coverage),
        ("Link and anchor integrity", check_links),
        ("Navigation coverage", check_navigation),
    )
    total = 0
    for title, check in checks:
        problems = check()
        total += len(problems)
        status = "OK" if not problems else f"{len(problems)} problem(s)"
        print(f"{title}: {status}")
        for problem in problems:
            print(f"  - {problem}")

    print()
    if total:
        print(f"{total} documentation problem(s) found.")
        return 1
    print("Docs are consistent with the code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
