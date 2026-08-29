# Harness Evaluator — Design Specification

## Goal

Evaluate agentic coding harnesses (Claude Code, Codex, Pi, OpenCode, OMP) against
one or more models on a set of tasks, to identify which harnesses are most
token-efficient, task-effective, and time-effective.

The comparison is **product-level** (which harness to *use* with a given model),
not harness-logic-level (which would require a single framework with swappable
strategies). The harness's system prompt, tool set, context strategy, and safety
policy are part of what is being evaluated, not controlled for.

## Comparison Model

- **Within-model**: fix the model, vary the harness. "Which harness gets the most
  out of Sonnet?"
- **Within-harness**: fix the harness, vary the model. "Which model does OpenCode
  drive best?"
- **No cross-model Pareto**: token accounting is non-fungible across vendors, so
  cross-model Pareto fronts are not meaningful.

The harness × model matrix is sparse (vendor-locked harnesses). Only viable cells
are run. Comparisons are always within a fixed model or within a fixed harness.

## Metrics

| Metric | Definition | Notes |
|---|---|---|
| Effectiveness | Success rate with partial credit + error classification | Hidden tests (SWE track) or rubric+judge (open track) |
| Token efficiency | Input/output/cache-read/cache-write/reasoning tokens per task | Includes failed-run cost in "per success" denominator |
| Time efficiency | Wall-clock to completion + time to first solution attempt | Aborted runs = failures, not fast successes |
| Cost efficiency | $ per success (includes failed runs) | Uses a published per-token + cache cost model |
| Robustness | Variance across repeats, error recovery, failure modes | Mixed-effects model decomposes harness vs model variance |
| Transparency | Gateway vs self-report token discrepancy | A harness that under-reports gets flagged |

## Task Tracks (separate leaderboards, never cross-compared)

### SWE-bench-style track
- Repo + issue + hidden test patch
- Objective: graded pass/fail with partial credit
- Error classes: overfit, timeout, refusal, wrong-approach, partial, success

### Open-ended track
- Free-form tasks (build feature, debug, refactor)
- LLM judge: frozen model version, calibrated on held-out anchor set before each
  eval run, tested for prompt/position sensitivity
- Structural checks: compiles, tests pass, diff size sanity
- Judge version drift is reported metadata; if calibration fails, track is flagged
  unreliable for that run

## Token Accounting

**Primary**: custom HTTP/SSE proxy captures every provider call with full token
metadata (input, output, cache-read, cache-write, reasoning tokens), cost, latency,
and full request/response bodies.

**Fallback**: for harnesses that can't be proxied cleanly, use provider billing API
+ harness self-report. Discrepancies between sources are flagged as a transparency
metric.

**Sub-agent attribution**: only available for cooperating (open) harnesses where
trace IDs can be injected. For closed harnesses, total per-run spend is measured
reliably, but per-sub-agent breakdown is not.

**Streaming**: per-provider, per-version SSE parsers. Reasoning/cache/tool tokens
captured from provider usage metadata, not estimated via local tokenizers.

**Failed-run cost**: included in "per success" metrics. No cheap-by-failing.

## Observability Tiers

Every result row carries an `observability_tier`:
- **full**: open harness, all metadata captured (system prompt, tools, context
  strategy, sub-agent attribution, full token breakdown)
- **partial**: closed harness, provider traffic captured via proxy, no internal
  metadata
- **minimal**: closed harness, only total spend captured via billing API

Leaderboards can be filtered by tier. Comparisons across tiers are flagged.

## Isolation

- Docker container per eval cell, disposable
- Controlled network policy (block non-provider traffic unless task requires it)
- Hosted API models only for v1 (local models out of scope)
- Fresh repo checkout per cell

## Statistics

- Default: 5 repeats per (harness × model × task) cell
- Mixed-effects model: harness as fixed effect, task and run as random effects
- Report standard error, confidence intervals, variance decomposition
- If model sampling variance dominates harness variance for a cell, flag as "no
  significant difference detected" rather than ranking
- Leaderboards show uncertainty bands, not just point estimates

## Run Exit Classes

Every run is classified into one of:
- **PASS** — task succeeded (tests pass / judge approves)
- **FAIL** — task failed, non-retryable (wrong answer, assertion failure, final-answer mismatch)
- **RETRYABLE_KILL** — run killed by transient issue (rate limit, OOM, timeout, provider error). Retried with exponential backoff. Counted as failure (success=0.0) in the current implementation; the exit class is preserved for future reliability analysis.
- **NON_RETRYABLE_KILL** — run killed by non-transient issue (harness crash, config error). Counted as failure.

All exit classes enter the effectiveness significance tests. Kills are recorded as `success=0.0`. The exit class is preserved in the results database for later reliability analysis, but the current statistics module does not filter by exit class.

## Resumability

- Cell-level only: skip already-completed cells, re-run incomplete ones from scratch
- Workdir cleaned and re-initialized on re-run (no preserved state from killed cells)
- RETRYABLE_KILL: retry with exponential backoff, up to 3 attempts
- NON_RETRYABLE_KILL or FAIL: counted as failure, not retried
- No mid-flight agent process resumption

## Architecture

```
CLI (Python)
  ├── heval run <config>      — execute an eval matrix
  ├── heval report <run-id>   — generate static report
  └── heval dashboard         — launch web dashboard

Orchestrator (Python)
  ├── matrix builder          — expand config into (harness × model × task × repeat) cells
  ├── budget caps             — stop when $ budget exhausted
  ├── cell-level resumability — skip completed, re-run incomplete
  └── statistics              — mixed-effects model, variance decomposition

Runner (Docker)
  ├── per-cell container       — fresh, disposable, network-controlled
  └── harness launch          — adapter installs + configures + runs harness

Harness Adapter Layer (Python + TS shims)
  ├── OpenCode adapter        — TS shim, full observability
  ├── Claude Code adapter     — env var proxy, partial observability
  ├── Codex adapter           — TS shim, partial observability
  ├── Pi adapter              — minimal observability
  └── OMP adapter             — minimal observability

Provider Gateway (Python)
  ├── HTTP/SSE proxy          — intercepts provider calls
  ├── per-provider parsers    — Anthropic, OpenAI streaming + usage
  ├── token capture           — in/out/cache-read/cache-write/reasoning
  ├── cost calculation        — published per-token + cache rates
  ├── trace_id injection      — for cooperating harnesses only
  └── billing API fallback    — for non-proxiable harnesses

Evaluator (Python)
  ├── SWE track               — hidden tests, partial credit, error classes
  ├── Open track              — frozen judge, rubric, structural checks
  └── error classification    — per-run failure mode

Results Store (SQLite)
  ├── per-cell metrics
  ├── raw traces              — full request/response logs
  └── harness metadata        — prompt, tools, config, observability tier

Reporting + Dashboard (Python/FastAPI)
  ├── within-model leaderboards
  ├── observability tier filtering
  ├── transparency/reconciliation flags
  ├── uncertainty bands, variance decomposition
  └── harness metadata display
```

## Deliverable

- **CLI** (`heval`): primary interface. Run evals, generate reports, launch dashboard.
- **Static reports**: HTML/JSON/CSV generated per run. Portable, shareable.
- **Web dashboard**: FastAPI backend, interactive exploration of results, live
  progress during runs.

## Scope Decisions

### In scope (v1)
- 5 harnesses: Claude Code, Codex, Pi, OpenCode, OMP
- 2 providers: Anthropic, OpenAI
- Both task tracks: SWE-bench-style, open-ended
- Custom task mix: SWE-bench subset + own open-ended tasks
- Docker container isolation
- Custom proxy + billing API fallback
- CLI + reports + dashboard

### Out of scope (v1)
- Local model servers
- Cross-model Pareto fronts
- Forcing identical system prompts across harnesses
- Mid-flight process resumption
- Pure harness-logic comparison (single framework, swappable strategies)
- Third provider (Google/Mistral/DeepSeek)

## Reconciliation

Token count reconciliation across proxy / billing API / self-report is treated as
**classification, not arithmetic**. For each harness:
- Define per-harness tolerance bands (e.g. ±2% for open harnesses, ±5% for closed)
- Publish unreconciled variance as a first-class metric
- If sources disagree beyond tolerance, flag the cell and use the highest-confidence source (proxy > billing > self-report)

## Judge Calibration

- Judge model version frozen per eval run, recorded as metadata
- Calibrated on held-out anchor set before each run
- Automated spot-checks (judge vs oracle) on every batch
- Recalibration triggered when anchor-set error drifts above fixed threshold
- If calibration fails, open-ended track flagged as unreliable for that run

## Build Sequence

### Milestone 1: Proxy canary (must pass before anything else)
Route one open harness (OpenCode) through the proxy with one provider (Anthropic).
Prove: proxy → SSE usage parsing → token reconciliation vs provider's own usage
response → report. Target <1% discrepancy. If this fails, stop and fix before
building anything else.

### Milestone 2: Core pipeline
Orchestrator + runner (Docker) + results store + evaluator (SWE track) + CLI +
static reports. End-to-end: config → matrix → run OpenCode on one task → evaluate
→ report.

### Milestone 3: Harness adapters (in order)
1. **OpenCode** — open, full observability. Validates proxy, reporting, Docker.
2. **Claude Code** — Anthropic, partial. Reuses Anthropic provider parser.
3. **Codex** — OpenAI, partial. Stress-tests provider-agnosticity, OpenAI usage quirks.
4. **Pi** — partial/minimal. Build after proxy/reconciliation pattern proven.
5. **OMP** — minimal observability, highest unknown risk. Build last, fallback to total-spend-only.

### Milestone 4: Open-ended track
Frozen judge + rubric + structural checks + calibration pipeline.

### Milestone 5: Dashboard
FastAPI interactive dashboard, live progress, leaderboard exploration.

### Milestone 6: Statistics
Mixed-effects model, variance decomposition, uncertainty bands, significance flagging.

## Minimal Task Set (for end-to-end pipeline validation)

Four task archetypes that stress the full pipeline:

1. **Deterministic micro-fix** — one failing unit test from a single off-by-one or
   string-format bug. Checks: exactness, token cost, runtime.
2. **Spec-driven feature addition** — add a function/CLI flag per a markdown spec;
   hidden test suite validates. Checks: read, plan, implement against spec.
3. **Cross-file behavioral-preserving refactor** — extract duplicated code across 3
   files into a shared helper; existing tests must pass. Checks: multi-file editing,
   regression avoidance.
4. **Open-ended design task** — "design and implement a small caching decorator,
   add tests, write a short README." Checks: LLM judge, sub-agent attribution,
   cost-per-step tracking.

## Honest Limitations

1. **Closed harnesses are partially observable.** We can capture their provider
   traffic (if proxyable) and total spend, but not their internal system prompt,
   context strategy, or sub-agent structure. Comparisons involving closed harnesses
   are product-level, not logic-level.

2. **Token efficiency across harnesses with different context strategies is not
   apples-to-apples.** A harness that pre-loads a repo map spends tokens on setup
   that another harness doesn't. We report raw counts and cost; interpretation
   requires reading the harness metadata.

3. **5 repeats may not separate harness variance from model sampling variance for
   all cells.** Cells where the difference is not statistically significant are
   flagged, not ranked.

4. **The open-ended track's LLM judge is a calibrated but imperfect ground truth.**
   Judge version is frozen and calibrated, but judge bias is not eliminable.
