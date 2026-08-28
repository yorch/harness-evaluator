"""Tests for the statistical analysis module."""

from __future__ import annotations

import numpy as np

from heval.stats import (
    BootstrapResult,
    ConsistencyResult,
    StatsAnalyzer,
    VarianceDecomposition,
    analyze_results,
)


def _make_results(
    n_harnesses: int = 2,
    n_models: int = 2,
    n_tasks: int = 5,
    n_repeats: int = 3,
    seed: int = 42,
) -> list[dict]:
    """Generate synthetic results for testing."""
    rng = np.random.default_rng(seed)
    results = []
    for h in range(n_harnesses):
        for m in range(n_models):
            for t in range(n_tasks):
                for r in range(n_repeats):
                    # Harness 0 is better than harness 1
                    base = 0.8 - h * 0.2
                    # Model 0 is slightly better
                    base += m * -0.05
                    # Task difficulty varies
                    base += (t - n_tasks / 2) * 0.02
                    # Add noise
                    success = float(np.clip(base + rng.normal(0, 0.05), 0, 1))
                    results.append({
                        "run_name": "test-run",
                        "harness": f"harness-{h}",
                        "model": f"model-{m}",
                        "task_id": f"task-{t}",
                        "repeat": r,
                        "exit_class": "pass" if success > 0.5 else "fail",
                        "success": success,
                        "total_cost": 0.001 * (h + 1),
                        "latency_ms": 5000 + h * 1000,
                        "input_tokens": 1000 + h * 100,
                        "output_tokens": 500 + h * 50,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "reasoning_tokens": 0,
                        "num_api_calls": 3,
                        "track": "swe",
                        "error_class": "success" if success > 0.7 else "partial",
                    })
    return results


class TestStatsAnalyzer:
    def test_empty_results(self):
        analyzer = StatsAnalyzer([])
        assert analyzer.variance_decomposition() is None
        assert analyzer.mixed_effects_model() is None
        assert analyzer.bootstrap_ci() == {}
        assert analyzer.consistency_analysis() == []

    def test_few_results(self):
        """Test that very small datasets are handled gracefully."""
        results = _make_results(n_harnesses=1, n_models=1, n_tasks=1, n_repeats=2)
        analyzer = StatsAnalyzer(results)
        # Should return None or a result with warnings, not crash
        vd = analyzer.variance_decomposition()
        # Either None (too few) or a valid result
        if vd:
            assert vd.total_variance >= 0

    def test_variance_decomposition(self):
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        vd = analyzer.variance_decomposition()
        assert vd is not None
        assert vd.total_variance > 0
        assert 0 <= vd.harness_pct <= 100
        assert 0 <= vd.model_pct <= 100
        assert 0 <= vd.task_pct <= 100
        assert 0 <= vd.residual_pct <= 100
        # Percentages are relative to sample variance, so they may not
        # sum to exactly 100 (components can overlap or be underestimated).
        # They should be non-negative and individually bounded.
        # The sum should be in a reasonable range (0-200%).
        total_pct = vd.harness_pct + vd.model_pct + vd.task_pct + vd.residual_pct
        assert 0 <= total_pct <= 200

    def test_mixed_effects_model(self):
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        me = analyzer.mixed_effects_model()
        assert me is not None
        assert me.n_observations > 0
        assert "C(harness)" in me.formula
        # Should have coefficients for harness and model
        assert len(me.coefficients) > 0

    def test_bootstrap_ci(self):
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        cis = analyzer.bootstrap_ci(metric="success", group_by="harness", n_bootstrap=100)
        assert len(cis) == 2  # Two harnesses
        for _harness, ci in cis.items():
            assert ci.point_estimate >= 0
            assert ci.ci_lower <= ci.point_estimate <= ci.ci_upper
            assert ci.n_bootstrap == 100

    def test_bootstrap_ci_reproducible(self):
        """Test that bootstrap CIs are reproducible with the same seed."""
        results = _make_results()
        analyzer1 = StatsAnalyzer(results)
        analyzer2 = StatsAnalyzer(results)
        cis1 = analyzer1.bootstrap_ci(seed=42, n_bootstrap=100)
        cis2 = analyzer2.bootstrap_ci(seed=42, n_bootstrap=100)
        for h in cis1:
            assert cis1[h].point_estimate == cis2[h].point_estimate
            assert cis1[h].ci_lower == cis2[h].ci_lower

    def test_consistency_analysis(self):
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        consistency = analyzer.consistency_analysis()
        assert len(consistency) > 0
        for c in consistency:
            assert c.harness.startswith("harness-")
            assert c.model.startswith("model-")
            assert c.n_repeats > 0
            assert c.min_success <= c.mean_success <= c.max_success
            assert c.cv_success >= 0
            if c.bootstrap_ci:
                assert c.bootstrap_ci.ci_lower <= c.bootstrap_ci.point_estimate
                assert c.bootstrap_ci.ci_upper >= c.bootstrap_ci.point_estimate

    def test_generate_report(self):
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        report = analyzer.generate_report()
        assert report.n_observations > 0
        assert report.variance_decomposition is not None
        assert report.mixed_effects is not None
        assert len(report.bootstrap_cis) > 0
        assert len(report.consistency) > 0

    def test_generate_report_warnings(self):
        """Test that warnings are generated for small datasets."""
        results = _make_results(n_harnesses=1, n_models=1, n_tasks=1, n_repeats=2)
        analyzer = StatsAnalyzer(results)
        report = analyzer.generate_report()
        assert len(report.warnings) > 0

    def test_analyze_results_convenience(self):
        results = _make_results()
        report = analyze_results(results)
        assert report.n_observations > 0


class TestVarianceDecomposition:
    def test_as_dict(self):
        vd = VarianceDecomposition(
            harness_variance=0.1,
            model_variance=0.05,
            task_variance=0.02,
            residual_variance=0.03,
            total_variance=0.2,
            harness_pct=50,
            model_pct=25,
            task_pct=10,
            residual_pct=15,
        )
        d = vd.as_dict()
        assert d["harness_variance"] == 0.1
        assert d["harness_pct"] == 50


class TestBootstrapResult:
    def test_as_dict(self):
        br = BootstrapResult(
            point_estimate=0.8,
            ci_lower=0.7,
            ci_upper=0.9,
            n_bootstrap=1000,
        )
        d = br.as_dict()
        assert d["point_estimate"] == 0.8
        assert d["ci_lower"] == 0.7
        assert d["ci_upper"] == 0.9
        assert d["n_bootstrap"] == 1000


class TestConsistencyResult:
    def test_as_dict(self):
        cr = ConsistencyResult(
            harness="h1",
            model="m1",
            mean_success=0.8,
            std_success=0.1,
            cv_success=0.125,
            n_repeats=3,
            min_success=0.7,
            max_success=0.9,
        )
        d = cr.as_dict()
        assert d["harness"] == "h1"
        assert d["mean_success"] == 0.8
        assert d["bootstrap_ci"] is None


class TestEdgeCases:
    """Tests for degenerate data and edge cases."""

    def test_all_identical_success(self):
        """Test variance decomposition when all success values are identical."""
        results = []
        for h in range(2):
            for m in range(2):
                for t in range(5):
                    for r in range(3):
                        results.append({
                            "run_name": "test",
                            "harness": f"h-{h}",
                            "model": f"m-{m}",
                            "task_id": f"t-{t}",
                            "repeat": r,
                            "success": 0.5,  # All identical
                            "exit_class": "pass",
                            "total_cost": 0.001,
                            "latency_ms": 1000,
                        })
        analyzer = StatsAnalyzer(results)
        vd = analyzer.variance_decomposition()
        assert vd is not None
        assert vd.total_variance == 0
        assert vd.residual_pct == 100

    def test_single_harness(self):
        """Test with only one harness level."""
        results = _make_results(n_harnesses=1, n_models=2, n_tasks=5, n_repeats=3)
        analyzer = StatsAnalyzer(results)
        vd = analyzer.variance_decomposition()
        assert vd is not None
        # Should not crash, harness variance should be 0 or near-0
        assert vd.harness_pct >= 0

    def test_single_model(self):
        """Test with only one model level."""
        results = _make_results(n_harnesses=2, n_models=1, n_tasks=5, n_repeats=3)
        analyzer = StatsAnalyzer(results)
        vd = analyzer.variance_decomposition()
        assert vd is not None
        assert vd.model_pct >= 0

    def test_single_task(self):
        """Test with only one task."""
        results = _make_results(n_harnesses=2, n_models=2, n_tasks=1, n_repeats=3)
        analyzer = StatsAnalyzer(results)
        vd = analyzer.variance_decomposition()
        assert vd is not None

    def test_bootstrap_n_one(self):
        """Test bootstrap CI with a single observation per group."""
        results = [
            {"harness": "h1", "model": "m1", "task_id": "t1", "repeat": 0,
             "success": 0.8, "exit_class": "pass"},
        ]
        analyzer = StatsAnalyzer(results)
        cis = analyzer.bootstrap_ci(metric="success", group_by="harness")
        assert "h1" in cis
        ci = cis["h1"]
        # For n=1, CI should equal the point estimate
        assert ci.point_estimate == 0.8
        assert ci.ci_lower == 0.8
        assert ci.ci_upper == 0.8

    def test_bootstrap_n_zero(self):
        """Test bootstrap CI with no data."""
        analyzer = StatsAnalyzer([])
        cis = analyzer.bootstrap_ci()
        assert cis == {}

    def test_json_serializable(self):
        """Test that report.as_dict() is JSON-serializable."""
        import json

        results = _make_results()
        analyzer = StatsAnalyzer(results)
        report = analyzer.generate_report()
        d = report.as_dict()
        # Should not raise
        json.dumps(d)

    def test_variance_decomposition_no_negative(self):
        """Test that variance components are never negative."""
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        vd = analyzer.variance_decomposition()
        assert vd is not None
        assert vd.harness_variance >= 0
        assert vd.model_variance >= 0
        assert vd.task_variance >= 0
        assert vd.residual_variance >= 0

    def test_r_squared_not_zero(self):
        """Test that R² is computed (not always 0)."""
        results = _make_results()
        analyzer = StatsAnalyzer(results)
        me = analyzer.mixed_effects_model()
        assert me is not None
        # With clear harness/model effects, R² should be > 0
        assert me.r_squared > 0

    def test_missing_columns(self):
        """Test that missing columns are handled gracefully."""
        results = [{"harness": "h1", "success": 0.5}]
        analyzer = StatsAnalyzer(results)
        # Should not crash
        vd = analyzer.variance_decomposition()
        # May return None due to missing model/task_id columns
        assert vd is None or vd.total_variance >= 0
