# Changelog

## [0.14.0](https://github.com/yorch/harness-evaluator/compare/v0.13.0...v0.14.0) (2026-09-01)


### Features

* **cli:** add check-keys command and surface judge errors in calibrate ([#49](https://github.com/yorch/harness-evaluator/issues/49)) ([f438043](https://github.com/yorch/harness-evaluator/commit/f438043b751a7a4aeb137fe8fc18a52696dd42ea))
* **run:** isolate runs per results DB, correct budget accounting, fix TUI and CLI progress ([#50](https://github.com/yorch/harness-evaluator/issues/50)) ([ca15e64](https://github.com/yorch/harness-evaluator/commit/ca15e640738cf5fd70d2461d18918f8734e1ad1d))


### Bug Fixes

* **runner:** run containers as the invoking user and resolve task repos via the library root ([#52](https://github.com/yorch/harness-evaluator/issues/52)) ([33d1601](https://github.com/yorch/harness-evaluator/commit/33d16019ee004a8e0124666a68d3f0787069ac36))


### Documentation

* close CLI reference gaps, correct the docker_image default, drop decaying counts ([#53](https://github.com/yorch/harness-evaluator/issues/53)) ([6c443ff](https://github.com/yorch/harness-evaluator/commit/6c443fff059fbf1b154c641670da833e8a886792))
* correct SWE evaluator heuristics and dashboard auth in docs ([#54](https://github.com/yorch/harness-evaluator/issues/54)) ([b510252](https://github.com/yorch/harness-evaluator/commit/b510252a04ed2ae72efb75fd3d4c836d16ef5778))

## [0.13.0](https://github.com/yorch/harness-evaluator/compare/v0.12.0...v0.13.0) (2026-09-01)


### Features

* **cli:** add --no-tui flag to run command ([#46](https://github.com/yorch/harness-evaluator/issues/46)) ([721973a](https://github.com/yorch/harness-evaluator/commit/721973a95f6eb06c62ebb128263036f87cae5120))


### Bug Fixes

* **evaluator:** extract text block from thinking-capable model responses ([#48](https://github.com/yorch/harness-evaluator/issues/48)) ([7e4c3f7](https://github.com/yorch/harness-evaluator/commit/7e4c3f75bdd3f96f4e04394974e0699750ab79f8))

## [0.12.0](https://github.com/yorch/harness-evaluator/compare/v0.11.1...v0.12.0) (2026-08-31)


### Features

* **gateway:** update pricing table and examples to current-gen models ([#43](https://github.com/yorch/harness-evaluator/issues/43)) ([a2dcb7e](https://github.com/yorch/harness-evaluator/commit/a2dcb7e9855d22fbe894a561258abefc4e5d3972))


### Bug Fixes

* **adapter:** default claude-code to --dangerously-skip-permissions ([#44](https://github.com/yorch/harness-evaluator/issues/44)) ([64d1ff6](https://github.com/yorch/harness-evaluator/commit/64d1ff619f2cef03b12352650eacf572c28f23f0))
* **gateway:** add brotli dependency and strip Content-Encoding from responses ([#42](https://github.com/yorch/harness-evaluator/issues/42)) ([2eebe54](https://github.com/yorch/harness-evaluator/commit/2eebe54a53c9a891818fb65da014ed56bf12606c))
* **runner:** mount .claude.json and warn on gateway not Docker-accessible ([#40](https://github.com/yorch/harness-evaluator/issues/40)) ([d54ef65](https://github.com/yorch/harness-evaluator/commit/d54ef65a778a9f25a377c4186a010cb67d4b8ab6))

## [0.11.1](https://github.com/yorch/harness-evaluator/compare/v0.11.0...v0.11.1) (2026-08-31)


### Bug Fixes

* **runner:** chmod credential mounts for non-root container user ([#38](https://github.com/yorch/harness-evaluator/issues/38)) ([e63b81f](https://github.com/yorch/harness-evaluator/commit/e63b81f0d248612df47cc8b6acac1cc86649daa1))
* **tui:** show ticking elapsed time and all running cells in footer ([24edc19](https://github.com/yorch/harness-evaluator/commit/24edc19fb1710b3595a25915e38a1411d42bd440))


### Documentation

* document harness output capture and gateway startup errors ([#36](https://github.com/yorch/harness-evaluator/issues/36)) ([3dd49ec](https://github.com/yorch/harness-evaluator/commit/3dd49ec79f60de2b86a612295201cf990bb32eda))

## [0.11.0](https://github.com/yorch/harness-evaluator/compare/v0.10.0...v0.11.0) (2026-08-31)


### Features

* **runner:** capture and display harness stdout/stderr in dashboard ([#35](https://github.com/yorch/harness-evaluator/issues/35)) ([34b27a2](https://github.com/yorch/harness-evaluator/commit/34b27a2b2eb6257a8ccd6d22bf232a4d44f7b307))


### Bug Fixes

* **gateway:** user-friendly error when port is already in use ([#34](https://github.com/yorch/harness-evaluator/issues/34)) ([1b06cd1](https://github.com/yorch/harness-evaluator/commit/1b06cd139634aab724e086bef0e25549bac08d51))


### Documentation

* fix remaining stale references and clarify Docker image contents ([#33](https://github.com/yorch/harness-evaluator/issues/33)) ([f787805](https://github.com/yorch/harness-evaluator/commit/f7878053c17f88b908d6f8c82c16d0b9a25610f4))
* sync documentation with recent features and adapters ([5f44388](https://github.com/yorch/harness-evaluator/commit/5f44388d89a1c940e13be6f73f6593acf1edea80))

## [0.10.0](https://github.com/yorch/harness-evaluator/compare/v0.9.0...v0.10.0) (2026-08-31)


### Features

* **dashboard:** overhaul UI/UX with a11y, dark mode, cell detail, sort, export ([#30](https://github.com/yorch/harness-evaluator/issues/30)) ([cfb6ca5](https://github.com/yorch/harness-evaluator/commit/cfb6ca5f46509f97faa5fb4965f28215cd8ec66d))

## [0.9.0](https://github.com/yorch/harness-evaluator/compare/v0.8.1...v0.9.0) (2026-08-31)


### Features

* **adapters:** add Gemini, Aider, Copilot, Antigravity, Cursor, Kiro adapters ([#29](https://github.com/yorch/harness-evaluator/issues/29)) ([275643e](https://github.com/yorch/harness-evaluator/commit/275643ec4b613b7333fe4a5692e91bf3c0a3740f))
* **cli:** list available runs when results command has no run name ([9c9245b](https://github.com/yorch/harness-evaluator/commit/9c9245be61ebb5c033607cfddf6808250cb00128))
* **cli:** make report and stats run_name optional, list runs when omitted ([#27](https://github.com/yorch/harness-evaluator/issues/27)) ([c6e75bd](https://github.com/yorch/harness-evaluator/commit/c6e75bd5271a56e2906535418cdcc132dbbb4f8f))
* **cli:** print next-steps commands after run completion ([7c6cc33](https://github.com/yorch/harness-evaluator/commit/7c6cc331ee197ad9ad04df28757a5b423f02b762))
* **dashboard:** surface error details across all user-facing surfaces ([#28](https://github.com/yorch/harness-evaluator/issues/28)) ([37067c1](https://github.com/yorch/harness-evaluator/commit/37067c1f2f181895c0e6d45d59fbbf80e51043dd))


### Bug Fixes

* **cli:** pass --db to all next-steps commands, not just dashboard ([6500eac](https://github.com/yorch/harness-evaluator/commit/6500eac031bd346cbb3d76cfb472b1b0aa66d3ba))
* **cli:** show resumability warning before TUI starts ([29f9a94](https://github.com/yorch/harness-evaluator/commit/29f9a94d25bcf97ce4f5731e02ca9b832df35632))
* **orchestrator:** track and display skip reasons in run summary ([f7f739a](https://github.com/yorch/harness-evaluator/commit/f7f739a025d1ff5ce1dc4319e3337f559342d871))

## [0.8.1](https://github.com/yorch/harness-evaluator/compare/v0.8.0...v0.8.1) (2026-08-31)


### Bug Fixes

* remove accidentally committed workdir and add to .gitignore ([794a00e](https://github.com/yorch/harness-evaluator/commit/794a00e4a09f9f9db7b28408a3240297b15c3023))
* **tui:** same-thread log writes and auto-exit on eval completion ([88c6c1e](https://github.com/yorch/harness-evaluator/commit/88c6c1e170a5002e349a1e804f6d76c2aff7bf11))

## [0.8.0](https://github.com/yorch/harness-evaluator/compare/v0.7.1...v0.8.0) (2026-08-31)


### Features

* **tui:** add Textual TUI with scrolling logs and fixed progress footer ([ec2c779](https://github.com/yorch/harness-evaluator/commit/ec2c77989aca176e5a47d8eda8a19041ca6b7d0f))


### Bug Fixes

* **tui:** remove unused import causing CI ruff failure ([83bd9c9](https://github.com/yorch/harness-evaluator/commit/83bd9c94e229f4c567c98abeab43b7720d41dc71))

## [0.7.1](https://github.com/yorch/harness-evaluator/compare/v0.7.0...v0.7.1) (2026-08-31)


### Bug Fixes

* **gateway:** use path-based trace ID to survive HTTP client URL joining ([#22](https://github.com/yorch/harness-evaluator/issues/22)) ([b97609e](https://github.com/yorch/harness-evaluator/commit/b97609e80d1dab53105c8b8a25f576b28b74d6c7))

## [0.7.0](https://github.com/yorch/harness-evaluator/compare/v0.6.1...v0.7.0) (2026-08-31)


### Features

* **gateway:** add periodic stats summary and update AGENTS.md ([#20](https://github.com/yorch/harness-evaluator/issues/20)) ([d96952f](https://github.com/yorch/harness-evaluator/commit/d96952f131936fb4481f8c26f5c64b3182e6dd37))

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
