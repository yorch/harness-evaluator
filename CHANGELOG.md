# Changelog

## [0.1.0](https://github.com/yorch/harness-evaluator/releases/tag/v0.1.0) - 2026-08-30

### Features

- Initial release of harness-evaluator
- Compare agentic coding harnesses (Claude Code, Codex, Pi, OpenCode, OMP) on token efficiency, task effectiveness, and time efficiency
- Gateway proxy for token/cost/latency accounting (aiohttp-based)
- Docker-based run isolation with per-cell containers
- SWE-bench-style evaluator with hidden tests and partial credit
- Open-ended LLM judge track with rubric scoring
- Mixed-effects statistical model, variance decomposition, and bootstrap CIs
- SQLite-backed results and gateway call storage
- FastAPI interactive dashboard with Jinja2 templates
- Static HTML/JSON/CSV report generation
- Astro + Starlight documentation site
- 20 bundled tasks (12 SWE + 8 open-ended) in Python and TypeScript
- 301 tests passing
- Published to PyPI as `harness-evaluator`
- Docker runner image at `ghcr.io/yorch/harness-evaluator-runner`
