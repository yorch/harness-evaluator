---
title: Statistics
description: Mixed-effects models, variance decomposition, bootstrap confidence intervals, and consistency analysis for eval results.
---

# Statistics

heval provides statistical analysis of evaluation results to determine whether differences between harnesses are statistically significant or could be explained by sampling variance.

## Overview

```bash
heval stats my-run --db heval_results.db
```

The statistics module (`src/heval/stats/__init__.py`) provides four analyses:

1. **Variance decomposition** — how much variance in success is explained by harness, model, task, and residual
2. **Mixed-effects model** — fixed effects for harness and model, random effect for task
3. **Bootstrap confidence intervals** — non-parametric CIs for success rate by harness
4. **Consistency analysis** — per harness × model: mean, std, CV, min/max, bootstrap CI

## Mixed-effects model

### Formula

```
success ~ C(harness) + C(model)
```

- **Fixed effects**: harness and model (categorical)
- **Random effect**: task (random intercept via `groups=df["task_id"]`, to account for task difficulty variation)

The formula passed to `mixedlm` is `success ~ C(harness) + C(model)`, with `groups=df["task_id"]` supplying the random intercept for task. This is equivalent to `success ~ C(harness) + C(model) + (1|task)` in lme4 notation.

The model is fit with REML (Restricted Maximum Likelihood) using `statsmodels.formula.api.mixedlm` with the `lbfgs` optimizer.

### Interpretation

- **Coefficients**: estimated effect of each harness/model level relative to the reference level. Positive coefficients indicate higher success rates.
- **Standard errors**: uncertainty in the coefficient estimates.
- **p-values**: significance of each coefficient. `***` = p<0.001, `**` = p<0.01, `*` = p<0.05.
- **R²**: pseudo R-squared = 1 - residual_var / total_var. Measures how much of the variance is explained by the model.
- **Random effects**: task-level intercepts showing how much each task shifts the expected success rate.

### Warnings

The model may fail to converge on small or degenerate datasets. When this happens, the `convergence_warning` field is set and a warning is displayed:

```
Warning: Mixed-effects model warning: Singular matrix
```

`statsmodels` may also emit `SingularMatrixWarning` and `ConvergenceWarning` — these are expected on small datasets and are not test failures.

## Variance decomposition

Partitions the variance in `success` into four components:

| Component | Description |
|-----------|-------------|
| Harness | Variance explained by harness choice |
| Model | Variance explained by model choice |
| Task | Variance from task difficulty (random effect) |
| Residual | Within-cell variance (unexplained) |

### Method

1. Fit the mixed-effects model: `success ~ C(harness) + C(model)` with `groups=task_id` (random intercept per task)
2. Extract variance components:
   - **Task variance**: random effect variance (`cov_re`)
   - **Residual variance**: `mdf.scale`
   - **Fixed variance**: variance of the fixed-effect prediction (`X @ beta`)
3. Split fixed variance between harness and model using group-mean variance ratios
4. Clamp negative estimates to zero
5. Percentages are relative to the **sample variance** of success (not the sum of components)

### Fallback

If the mixed-effects model fails (e.g., singular matrix), a simple group-mean variance decomposition is used:

- `harness_var` = variance of harness group means
- `model_var` = variance of model group means
- `task_var` = variance of task group means
- `residual_var` = sample_var - (harness + model + task)

Single-level factors (e.g., only one model) produce NaN from `var(ddof=1)` and are treated as 0.

### Interpretation

- **High harness variance** → harness choice matters a lot for success
- **High task variance** → task difficulty dominates (expected — some tasks are harder than others)
- **High residual variance** → unexplained variance (sampling noise, harness × task interactions)
- **Low harness variance** → harnesses perform similarly; differences may not be significant

## Bootstrap confidence intervals

Non-parametric bootstrap CIs for success rate, grouped by harness.

### Method

1. For each harness, take all success values
2. Resample with replacement `n_bootstrap` times (default: 1000)
3. Calculate the mean of each resample
4. CI lower = 2.5th percentile, CI upper = 97.5th percentile (for 95% CI)
5. Clamp to [0, 1] for success metrics

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `metric` | `"success"` | Column to compute CI for |
| `group_by` | `"harness"` | Column to group by |
| `n_bootstrap` | 1000 | Number of bootstrap resamples |
| `confidence` | 0.95 | Confidence level (0–1) |
| `seed` | 42 | Random seed for reproducibility |

### Single observation

With only one observation, the CI is the point estimate itself (no variance to bootstrap).

### Interpretation

- **Non-overlapping CIs** between two harnesses → statistically significant difference
- **Overlapping CIs** → difference may not be significant; do not rank
- **Wide CIs** → high uncertainty, need more repeats

## Consistency analysis

Per harness × model combination, reports:

| Metric | Description |
|---------|-------------|
| Mean success | Average success rate |
| Std success | Standard deviation |
| CV (coefficient of variation) | Std / mean — relative variability |
| N repeats | Number of observations |
| Min success | Lowest success rate |
| Max success | Highest success rate |
| Bootstrap CI | 95% CI for the mean |

### Interpretation

- **Low CV** → harness is consistent across repeats
- **High CV** → harness is inconsistent; results may not be reliable
- **Large range (max - min)** → high variability in performance

## Warnings

The `StatisticalReport` includes automatic warnings:

| Warning | Condition |
|---------|-----------|
| Small sample | `n < 30` — mixed-effects model may be unreliable |
| No data | `n == 0` |
| Convergence failure | Mixed-effects model failed to converge |

## Statistical report structure

```python
@dataclass
class StatisticalReport:
    variance_decomposition: VarianceDecomposition | None
    mixed_effects: MixedEffectsResult | None
    bootstrap_cis: dict[str, BootstrapResult]
    consistency: list[ConsistencyResult]
    n_observations: int
    warnings: list[str]
```

All result dataclasses have `as_dict()` methods for JSON serialization, with NaN/inf values converted to `None`.

## CLI output example

```
Statistical Analysis: broad-first-pass
Observations: 300

Variance Decomposition
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Component   ┃    Variance   ┃ % of Total ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Harness     │     0.023400  │      15.2% │
│ Model       │     0.015600  │      10.1% │
│ Task        │     0.078000  │      50.7% │
│ Residual    │     0.036900  │      24.0% │
└─────────────┴───────────────┴────────────┘

Mixed-Effects Model
Formula: success ~ C(harness) + C(model)
R²: 0.7600
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Coefficient                      ┃   Estimate ┃ Std Error ┃    p-value   ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ Intercept                        │   0.750000 │  0.050000 │     0.0000 ***│
│ C(harness)[T.claude-code]        │  -0.120000 │  0.060000 │     0.0456 *  │
│ C(harness)[T.codex]              │  -0.080000 │  0.060000 │     0.1813    │
│ C(model)[T.gpt-4o]               │  -0.150000 │  0.050000 │     0.0028 ** │
└──────────────────────────────────┴────────────┴───────────┴──────────────┘

Bootstrap 95% CIs (Success by Harness)
┏━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Harness     ┃  Mean  ┃ CI Lower ┃ CI Upper ┃
┡━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ opencode    │ 0.8500 │   0.7800 │   0.9100 │
│ claude-code │ 0.6300 │   0.5500 │   0.7100 │
│ codex       │ 0.6700 │   0.5900 │   0.7500 │
│ pi          │ 0.5200 │   0.4400 │   0.6000 │
│ omp         │ 0.4800 │   0.4000 │   0.5600 │
└─────────────┴────────┴──────────┴──────────┘

Consistency Analysis
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━┓
┃ Harness     ┃ Model              ┃  Mean  ┃  Std   ┃   CV   ┃  N  ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━┩
│ opencode    │ claude-sonnet-4-.. │ 0.8500 │ 0.0800 │ 0.0941 │  30 │
│ claude-code │ claude-sonnet-4-.. │ 0.6300 │ 0.1200 │ 0.1905 │  30 │
└─────────────┴────────────────────┴────────┴────────┴────────┴─────┘
```

## Dependencies

The statistics module depends on:

- `pandas` — DataFrame operations
- `statsmodels` — mixed-effects model (`mixedlm`)
- `numpy` — bootstrap resampling and percentile calculations

These are listed in `pyproject.toml` as production dependencies.

## Key source files

| File | Description |
|------|-------------|
| `src/heval/stats/__init__.py` | `StatsAnalyzer`, `StatisticalReport`, all result dataclasses, `analyze_results()` |

## Honest limitations

1. **5 repeats may not separate harness variance from model sampling variance** for all cells. Cells where the difference is not statistically significant are flagged, not ranked.
2. **The mixed-effects model assumes independent observations**. Repeats of the same cell are not independent (same harness, model, task), but the model treats them as such. This is a simplification.
3. **Bootstrap CIs are non-parametric** and make no distributional assumptions, but they are limited by the number of observations. With 5 repeats per cell, the CI is wide.
4. **Variance decomposition percentages are relative to sample variance**, not the sum of components. This means percentages may not sum to 100% due to estimation differences.
