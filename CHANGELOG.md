# Changelog

## [0.6.1](https://github.com/yorch/harness-evaluator/compare/v0.6.0...v0.6.1) (2026-08-31)


### Bug Fixes

* **config:** default docker_image to :latest instead of version-pinned ([0a262db](https://github.com/yorch/harness-evaluator/commit/0a262db4eddb0ac9904880b4fd4cb750938b82f5))

## [0.6.0](https://github.com/yorch/harness-evaluator/compare/v0.5.0...v0.6.0) (2026-08-31)


### Features

* **cli:** add --version flag and version command ([b43bc05](https://github.com/yorch/harness-evaluator/commit/b43bc050fc51c23d4b1415d74350811c743e41b2))

## [0.5.0](https://github.com/yorch/harness-evaluator/compare/v0.4.0...v0.5.0) (2026-08-31)


### Features

* **dashboard:** add optional token authentication with cookie flow ([#16](https://github.com/yorch/harness-evaluator/issues/16)) ([38dd017](https://github.com/yorch/harness-evaluator/commit/38dd017df962e825f99bd855017157051fee381e))

## [0.4.0](https://github.com/yorch/harness-evaluator/compare/v0.3.3...v0.4.0) (2026-08-31)


### Features

* **cli:** add live progress panel and configurable logging to run/gateway ([#13](https://github.com/yorch/harness-evaluator/issues/13)) ([e413c83](https://github.com/yorch/harness-evaluator/commit/e413c838e746a74b7b0562ad24dd03a910fad62a))
* expand pricing table, persist calibration anchors, document TS shim decision ([#12](https://github.com/yorch/harness-evaluator/issues/12)) ([f8ebc2b](https://github.com/yorch/harness-evaluator/commit/f8ebc2be48cbe88fa81e23614334b98b5a85e37a))
* wire gateway-vs-self-report reconciliation into eval loop ([#15](https://github.com/yorch/harness-evaluator/issues/15)) ([79dbc76](https://github.com/yorch/harness-evaluator/commit/79dbc76bac1c618d46e3d790facd7eb470b4cbb5))


### Bug Fixes

* **runner:** ignore dangling symlinks when copying credential dir ([#14](https://github.com/yorch/harness-evaluator/issues/14)) ([2cd78fc](https://github.com/yorch/harness-evaluator/commit/2cd78fcc28fd8ec09115455e6b381ae0a75e33eb))


### Documentation

* add subscription auth guide and fix doc inaccuracies ([#10](https://github.com/yorch/harness-evaluator/issues/10)) ([3190bca](https://github.com/yorch/harness-evaluator/commit/3190bca486a55f34c7222163ba81a913cfaaaae0))

## [0.3.3](https://github.com/yorch/harness-evaluator/compare/v0.3.2...v0.3.3) (2026-08-31)


### Documentation

* document PyPI trusted publishing requirement ([427c455](https://github.com/yorch/harness-evaluator/commit/427c455f8c0525bc7a9c08a88bfb544d663f0013))

## [0.3.2](https://github.com/yorch/harness-evaluator/compare/v0.3.1...v0.3.2) (2026-08-31)


### Bug Fixes

* **ci:** use raw tag from release-please output for Docker metadata ([e799ec3](https://github.com/yorch/harness-evaluator/commit/e799ec304c3abb015eac7960ec6e6a2bab7eef29))

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
