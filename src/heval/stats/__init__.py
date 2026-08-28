"""Statistical analysis of harness evaluation results.

Provides:
  - Mixed-effects modeling (harness and model as fixed effects, task as random)
  - Variance decomposition (harness, model, task, residual components)
  - Uncertainty bands (bootstrap confidence intervals)
  - Repeated-run consistency analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf


@dataclass
class VarianceDecomposition:
    """Variance decomposition results."""

    harness_variance: float
    model_variance: float
    task_variance: float
    residual_variance: float
    total_variance: float
    harness_pct: float
    model_pct: float
    task_pct: float
    residual_pct: float

    def as_dict(self) -> dict[str, float | None]:
        def _safe(v: float) -> float | None:
            f = float(v)
            return None if (np.isnan(f) or np.isinf(f)) else f

        return {
            "harness_variance": _safe(self.harness_variance),
            "model_variance": _safe(self.model_variance),
            "task_variance": _safe(self.task_variance),
            "residual_variance": _safe(self.residual_variance),
            "total_variance": _safe(self.total_variance),
            "harness_pct": _safe(self.harness_pct),
            "model_pct": _safe(self.model_pct),
            "task_pct": _safe(self.task_pct),
            "residual_pct": _safe(self.residual_pct),
        }


@dataclass
class MixedEffectsResult:
    """Mixed-effects model results."""

    formula: str
    coefficients: dict[str, float]
    std_errors: dict[str, float]
    p_values: dict[str, float]
    confidence_intervals: dict[str, tuple[float, float]]
    random_effects: dict[str, dict[str, float]]
    r_squared: float
    n_observations: int
    convergence_warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        def _safe_float(v: Any) -> float | None:
            """Convert to float, returning None for NaN/inf."""
            try:
                f = float(v)
                if np.isnan(f) or np.isinf(f):
                    return None
                return f
            except (TypeError, ValueError):
                return None

        return {
            "formula": self.formula,
            "coefficients": {k: _safe_float(v) for k, v in self.coefficients.items()},
            "std_errors": {k: _safe_float(v) for k, v in self.std_errors.items()},
            "p_values": {k: _safe_float(v) for k, v in self.p_values.items()},
            "confidence_intervals": {
                k: [_safe_float(v[0]), _safe_float(v[1])]
                for k, v in self.confidence_intervals.items()
            },
            "random_effects": self.random_effects,
            "r_squared": _safe_float(self.r_squared),
            "n_observations": self.n_observations,
            "convergence_warning": self.convergence_warning,
        }


@dataclass
class BootstrapResult:
    """Bootstrap confidence interval results."""

    point_estimate: float
    ci_lower: float
    ci_upper: float
    n_bootstrap: int
    bootstrap_samples: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "point_estimate": self.point_estimate,
            "ci_lower": self.ci_lower,
            "ci_upper": self.ci_upper,
            "n_bootstrap": self.n_bootstrap,
        }


@dataclass
class ConsistencyResult:
    """Repeated-run consistency results."""

    harness: str
    model: str
    mean_success: float
    std_success: float
    cv_success: float  # coefficient of variation
    n_repeats: int
    min_success: float
    max_success: float
    bootstrap_ci: BootstrapResult | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "model": self.model,
            "mean_success": self.mean_success,
            "std_success": self.std_success,
            "cv_success": self.cv_success,
            "n_repeats": self.n_repeats,
            "min_success": self.min_success,
            "max_success": self.max_success,
            "bootstrap_ci": self.bootstrap_ci.as_dict() if self.bootstrap_ci else None,
        }


@dataclass
class StatisticalReport:
    """Complete statistical report."""

    variance_decomposition: VarianceDecomposition | None
    mixed_effects: MixedEffectsResult | None
    bootstrap_cis: dict[str, BootstrapResult]
    consistency: list[ConsistencyResult]
    n_observations: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "variance_decomposition": (
                self.variance_decomposition.as_dict()
                if self.variance_decomposition
                else None
            ),
            "mixed_effects": (
                self.mixed_effects.as_dict() if self.mixed_effects else None
            ),
            "bootstrap_cis": {
                k: v.as_dict() for k, v in self.bootstrap_cis.items()
            },
            "consistency": [c.as_dict() for c in self.consistency],
            "n_observations": self.n_observations,
            "warnings": self.warnings,
        }


class StatsAnalyzer:
    """Statistical analysis of harness evaluation results."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        """Initialize with results from ResultsStore.get_all_results().

        Expected fields: run_name, harness, model, task_id, repeat,
        exit_class, success, total_cost, latency_ms, etc.
        """
        self.df = pd.DataFrame(results)
        if not self.df.empty:
            # Ensure categorical columns
            for col in ["harness", "model", "task_id"]:
                if col in self.df.columns:
                    self.df[col] = self.df[col].astype("category")

    def variance_decomposition(self) -> VarianceDecomposition | None:
        """Decompose variance in success into harness, model, task, and residual.

        Uses a mixed-effects model: success ~ harness + model + (1|task).
        Variance components:
          - task_var: random effect variance (between-task variation)
          - residual_var: residual (within-cell) variance
          - fixed_var: variance explained by fixed effects (harness + model)
        The fixed_var is further split between harness and model using
        Type-I-style sequential SS on the fixed-effect prediction.

        Percentages are relative to the **sample variance** of success,
        not the sum of components (which can differ due to estimation).
        """
        if self.df.empty or "success" not in self.df.columns:
            return None

        df = self.df.dropna(subset=["success"])
        if len(df) < 10:
            return None

        # Sample variance of success (the denominator for percentages)
        sample_var = float(df["success"].var())
        if sample_var == 0:
            return VarianceDecomposition(
                harness_variance=0, model_variance=0, task_variance=0,
                residual_variance=0, total_variance=0,
                harness_pct=0, model_pct=0, task_pct=0, residual_pct=100,
            )

        # Fit mixed-effects model: success ~ harness + model + (1|task)
        try:
            md = smf.mixedlm(
                "success ~ C(harness) + C(model)",
                data=df,
                groups=df["task_id"],
            )
            mdf = md.fit(reml=True, method="lbfgs")

            # Extract variance components
            task_var = float(mdf.cov_re.iloc[0, 0]) if hasattr(mdf.cov_re, "iloc") else 0.0
            residual_var = float(mdf.scale)

            # Fixed-effects variance: use FIXED prediction only (exblusdes random effects)
            # mdf.fittedvalues includes random effects, so we compute the fixed
            # prediction manually: X @ beta
            fixed_pred = mdf.model.exog @ mdf.fe_params.values
            fixed_var = float(np.var(fixed_pred, ddof=1))

            # Split fixed_var between harness and model using group-mean variances
            # as relative weights (avoids double-counting task variance)
            harness_var, model_var = self._partition_fixed_variance(df, fixed_var)

            # Clamp negative variance estimates to zero
            harness_var = max(0.0, harness_var)
            model_var = max(0.0, model_var)
            task_var = max(0.0, task_var)
            residual_var = max(0.0, residual_var)

            # Total is the sample variance (the meaningful denominator)
            total = sample_var
            if total == 0:
                total = 1e-10

            return VarianceDecomposition(
                harness_variance=harness_var,
                model_variance=model_var,
                task_variance=task_var,
                residual_variance=residual_var,
                total_variance=total,
                harness_pct=(harness_var / total) * 100,
                model_pct=(model_var / total) * 100,
                task_pct=(task_var / total) * 100,
                residual_pct=(residual_var / total) * 100,
            )
        except Exception as e:
            # Fallback: use simple group variances
            return self._simple_variance_decomposition(df, str(e))

    def _partition_fixed_variance(
        self, df: pd.DataFrame, fixed_var: float
    ) -> tuple[float, float]:
        """Split fixed-effect variance between harness and model.

        Uses the ratio of group-mean variances as relative weights.
        Does NOT use mdf.fittedvalues (which includes random effects).
        """
        harness_means = df.groupby("harness", observed=True)["success"].mean()
        model_means = df.groupby("model", observed=True)["success"].mean()

        harness_raw = float(harness_means.var()) if len(harness_means) > 1 else 0.0
        model_raw = float(model_means.var()) if len(model_means) > 1 else 0.0

        total = harness_raw + model_raw
        if total > 0 and fixed_var > 0:
            harness_var = (harness_raw / total) * fixed_var
            model_var = (model_raw / total) * fixed_var
        else:
            harness_var = 0.0
            model_var = 0.0

        return harness_var, model_var

    def _simple_variance_decomposition(
        self, df: pd.DataFrame, error: str
    ) -> VarianceDecomposition:
        """Fallback: simple group-mean variance decomposition.

        Handles single-level factors (which produce NaN from var(ddof=1))
        by treating their variance as 0.
        """
        sample_var = float(df["success"].var())
        if sample_var == 0:
            return VarianceDecomposition(
                harness_variance=0, model_variance=0, task_variance=0,
                residual_variance=0, total_variance=0,
                harness_pct=0, model_pct=0, task_pct=0, residual_pct=100,
            )

        def _safe_group_var(col: str) -> float:
            """Group-mean variance, returning 0 for single-level factors."""
            groups = df.groupby(col, observed=True)["success"].mean()
            if len(groups) <= 1:
                return 0.0
            v = float(groups.var())
            return v if not np.isnan(v) else 0.0

        harness_var = _safe_group_var("harness")
        model_var = _safe_group_var("model")
        task_var = _safe_group_var("task_id")

        # Clamp to non-negative and cap at sample_var
        harness_var = max(0.0, min(harness_var, sample_var))
        model_var = max(0.0, min(model_var, sample_var))
        task_var = max(0.0, min(task_var, sample_var))

        # Residual is what's left (clamped to >= 0)
        explained = harness_var + model_var + task_var
        residual_var = max(0.0, sample_var - explained)

        total = sample_var
        if total == 0:
            total = 1e-10

        return VarianceDecomposition(
            harness_variance=harness_var,
            model_variance=model_var,
            task_variance=task_var,
            residual_variance=residual_var,
            total_variance=total,
            harness_pct=(harness_var / total) * 100,
            model_pct=(model_var / total) * 100,
            task_pct=(task_var / total) * 100,
            residual_pct=(residual_var / total) * 100,
        )

    def mixed_effects_model(self) -> MixedEffectsResult | None:
        """Fit a mixed-effects model: success ~ harness + model + (1|task).

        Treats harness and model as fixed effects, task as a random effect
        (to account for task difficulty variation).
        """
        if self.df.empty or "success" not in self.df.columns:
            return None

        df = self.df.dropna(subset=["success"])
        if len(df) < 10:
            return None

        formula = "success ~ C(harness) + C(model)"

        try:
            md = smf.mixedlm(formula, data=df, groups=df["task_id"])
            mdf = md.fit(reml=True, method="lbfgs")

            # Extract fixed effects — only keep fixed-effect params (not variance)
            fe_names = list(mdf.fe_params.index)
            coeffs = {str(k): float(v) for k, v in mdf.fe_params.items()}
            std_errs = {str(k): float(v) for k, v in mdf.bse_fe.items()}
            # p_values includes variance params; filter to fixed effects only
            p_values = {
                str(k): float(v)
                for k, v in mdf.pvalues.items()
                if str(k) in fe_names
            }
            conf_int = {}
            for name, ci in mdf.conf_int().iterrows():
                if str(name) in fe_names:
                    conf_int[str(name)] = (float(ci[0]), float(ci[1]))

            # Extract random effects (task-level intercepts)
            random_effects: dict[str, dict[str, float]] = {}
            try:
                re = mdf.random_effects
                for task_id, effects in re.items():
                    if hasattr(effects, "items"):
                        random_effects[str(task_id)] = {
                            str(k): float(v) for k, v in effects.items()
                        }
                    else:
                        random_effects[str(task_id)] = {"intercept": float(effects)}
            except Exception:
                pass

            # Pseudo R-squared: 1 - residual_var / total_var
            total_var = float(df["success"].var())
            residual_var = float(mdf.scale)
            r_squared = max(0.0, 1.0 - residual_var / total_var) if total_var > 0 else 0.0

            return MixedEffectsResult(
                formula=formula,
                coefficients=coeffs,
                std_errors=std_errs,
                p_values=p_values,
                confidence_intervals=conf_int,
                random_effects=random_effects,
                r_squared=r_squared,
                n_observations=len(df),
            )
        except Exception as e:
            return MixedEffectsResult(
                formula=formula,
                coefficients={},
                std_errors={},
                p_values={},
                confidence_intervals={},
                random_effects={},
                r_squared=0.0,
                n_observations=len(df),
                convergence_warning=str(e),
            )

    def bootstrap_ci(
        self,
        metric: str = "success",
        group_by: str = "harness",
        n_bootstrap: int = 1000,
        confidence: float = 0.95,
        seed: int | None = 42,
    ) -> dict[str, BootstrapResult]:
        """Bootstrap confidence intervals for a metric grouped by a factor.

        Args:
            metric: Column to compute CI for (e.g., "success", "total_cost")
            group_by: Column to group by (e.g., "harness", "model")
            n_bootstrap: Number of bootstrap resamples
            confidence: Confidence level (0-1)
            seed: Random seed for reproducibility
        """
        if self.df.empty or metric not in self.df.columns:
            return {}

        rng = np.random.default_rng(seed)
        alpha = 1 - confidence
        results: dict[str, BootstrapResult] = {}

        for group_name, group_df in self.df.groupby(group_by):
            values = group_df[metric].dropna().values
            n = len(values)
            if n == 0:
                results[str(group_name)] = BootstrapResult(
                    point_estimate=0.0,
                    ci_lower=0.0,
                    ci_upper=0.0,
                    n_bootstrap=n_bootstrap,
                )
                continue
            if n == 1:
                # With a single observation, the CI is the point itself
                point = float(values[0])
                results[str(group_name)] = BootstrapResult(
                    point_estimate=point,
                    ci_lower=point,
                    ci_upper=point,
                    n_bootstrap=n_bootstrap,
                )
                continue

            point_est = float(values.mean())
            # Vectorized bootstrap: draw all resamples at once
            indices = rng.integers(0, n, size=(n_bootstrap, n))
            boot_samples = values[indices].mean(axis=1)

            ci_lower = float(np.percentile(boot_samples, 100 * alpha / 2))
            ci_upper = float(np.percentile(boot_samples, 100 * (1 - alpha / 2)))

            # Clamp to valid range for [0,1] metrics
            if metric == "success":
                ci_lower = max(0.0, ci_lower)
                ci_upper = min(1.0, ci_upper)

            results[str(group_name)] = BootstrapResult(
                point_estimate=point_est,
                ci_lower=ci_lower,
                ci_upper=ci_upper,
                n_bootstrap=n_bootstrap,
            )

        return results

    def consistency_analysis(
        self, n_bootstrap: int = 1000, seed: int | None = 42
    ) -> list[ConsistencyResult]:
        """Analyze consistency across repeats for each harness × model combination.

        Returns per-combination:
        - Mean success
        - Std success
        - Coefficient of variation
        - Min/max success
        - Bootstrap CI for the mean
        """
        if self.df.empty or "success" not in self.df.columns:
            return []

        rng = np.random.default_rng(seed)
        results: list[ConsistencyResult] = []

        for (harness, model), group_df in self.df.groupby(["harness", "model"]):
            vals = group_df["success"].dropna().values
            n = len(vals)
            if n == 0:
                continue

            mean_s = float(vals.mean())
            std_s = float(vals.std()) if n > 1 else 0.0
            cv = float(std_s / mean_s) if mean_s != 0 else 0.0

            # Bootstrap CI
            boot_ci = None
            if n >= 2:
                boot_samples = np.array([
                    float(rng.choice(vals, size=n, replace=True).mean())
                    for _ in range(n_bootstrap)
                ])
                boot_ci = BootstrapResult(
                    point_estimate=mean_s,
                    ci_lower=float(np.percentile(boot_samples, 2.5)),
                    ci_upper=float(np.percentile(boot_samples, 97.5)),
                    n_bootstrap=n_bootstrap,
                )

            results.append(
                ConsistencyResult(
                    harness=str(harness),
                    model=str(model),
                    mean_success=mean_s,
                    std_success=std_s,
                    cv_success=cv,
                    n_repeats=n,
                    min_success=float(vals.min()),
                    max_success=float(vals.max()),
                    bootstrap_ci=boot_ci,
                )
            )

        return results

    def generate_report(self) -> StatisticalReport:
        """Generate a complete statistical report."""
        warnings: list[str] = []
        n = len(self.df)

        if n < 30:
            warnings.append(
                f"Only {n} observations. Mixed-effects model may be unreliable."
            )
        if n == 0:
            warnings.append("No data available for analysis.")

        var_decomp = self.variance_decomposition()
        mixed = self.mixed_effects_model()
        if mixed and mixed.convergence_warning:
            warnings.append(f"Mixed-effects model warning: {mixed.convergence_warning}")

        boot_cis = self.bootstrap_ci(metric="success", group_by="harness")
        consistency = self.consistency_analysis()

        return StatisticalReport(
            variance_decomposition=var_decomp,
            mixed_effects=mixed,
            bootstrap_cis=boot_cis,
            consistency=consistency,
            n_observations=n,
            warnings=warnings,
        )


def analyze_results(results: list[dict[str, Any]]) -> StatisticalReport:
    """Convenience function: analyze results and return a statistical report."""
    analyzer = StatsAnalyzer(results)
    return analyzer.generate_report()
