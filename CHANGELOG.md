# Changelog

## [0.3.1](https://github.com/yorch/harness-evaluator/compare/v0.3.0...v0.3.1) (2026-08-31)


### Bug Fixes

* **ci:** move PyPI publish and Docker into release-please workflow ([#6](https://github.com/yorch/harness-evaluator/issues/6)) ([41490ab](https://github.com/yorch/harness-evaluator/commit/41490ab1d48deb192c5093f6e30a11757605f4fd))
* **ci:** use inputs.ref and GH_REPO in publish workflow_dispatch ([c20ead7](https://github.com/yorch/harness-evaluator/commit/c20ead7e4d046c98bc0c42905ddcb67f9a8f267a))

## [0.3.0](https://github.com/yorch/harness-evaluator/compare/v0.2.0...v0.3.0) (2026-08-31)


### Features

* **orchestrator:** add multi-phase task support with adversarial review ([#5](https://github.com/yorch/harness-evaluator/issues/5)) ([dc59aa9](https://github.com/yorch/harness-evaluator/commit/dc59aa9dd2eb579f6edfe7e70001986ca68bcbf5))


### Documentation

* make no-clone install path the primary guide ([#3](https://github.com/yorch/harness-evaluator/issues/3)) ([6644922](https://github.com/yorch/harness-evaluator/commit/6644922d91eeb7e3ee3662422f42a4669647d76d))

## [0.2.0](https://github.com/yorch/harness-evaluator/compare/v0.1.0...v0.2.0) (2026-08-30)


### Features

* **auth:** add subscription/OAuth auth mode support ([#1](https://github.com/yorch/harness-evaluator/issues/1)) ([7a879f9](https://github.com/yorch/harness-evaluator/commit/7a879f9dd337637864fb25f52130a3c79facc03c))
* **ci:** create GitHub Release with artifacts on tag push ([b78241a](https://github.com/yorch/harness-evaluator/commit/b78241a1887ed9d2716f85a81efdaa4715dbba8d))
* **site:** add llms.txt and llms-full.txt for LLM discoverability ([2fb50d6](https://github.com/yorch/harness-evaluator/commit/2fb50d681217f773346929a977024d8a2b9665e5))


### Bug Fixes

* **ci:** correct release-please-action SHA (typo a2435 → a2445) ([6883e20](https://github.com/yorch/harness-evaluator/commit/6883e20d00f4d9339311534c50c33bc97d9ac67b))

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
