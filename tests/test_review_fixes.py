"""Tests for review-fix batch: SWE no-tests, crash classification, judge
prompt injection, score clamping, path traversal, container name
sanitization, and progress lock."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from heval.evaluator.open_ended import (
    DEFAULT_RUBRIC,
    FrozenJudge,
    Rubric,
    RubricCriterion,
)
from heval.evaluator.swe import ErrorClass, SWEEvaluator
from heval.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunConfig,
    TaskSpec,
    TaskTrack,
)
from heval.reporting.static_report import (
    assert_safe_path,
    sanitize_id,
)
from heval.runner.docker import _sanitize_container_name

# ---------------------------------------------------------------------------
# SWE evaluator: no-tests = perfect pass fix
# ---------------------------------------------------------------------------


class TestSWENoTests:
    """A test command that runs zero tests must NOT receive a perfect score."""

    def test_zero_tests_returncode_zero_is_failure(self, tmp_path: Path):
        """returncode=0 with 0 parsed tests should be a fail, not a pass."""
        import subprocess

        evaluator = SWEEvaluator()
        repo = tmp_path / "repo"
        repo.mkdir()
        # Init a git repo and commit an initial file so _get_diff works.
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@local"], cwd=repo, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "test"], cwd=repo, capture_output=True
        )
        (repo / "solution.py").write_text("print('hello')\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, capture_output=True
        )
        # Now make a change so the diff is non-empty.
        (repo / "solution.py").write_text("print('hello world')\n")

        task = TaskSpec(
            id="t",
            name="t",
            track=TaskTrack.SWE,
            task_prompt="do thing",
            test_command="true",  # always exits 0, runs no tests
        )

        result = evaluator.evaluate(task, repo)
        assert result.success == 0.0
        assert result.exit_class == "fail"
        # Should be classified as CRASH (no tests collected)
        assert result.error_class == ErrorClass.CRASH


# ---------------------------------------------------------------------------
# SWE evaluator: crash classification reachable
# ---------------------------------------------------------------------------


class TestSWECrashClassification:
    """A non-zero exit with no parseable test output should be CRASH."""

    def test_nonzero_exit_no_tests_is_crash(self, tmp_path: Path):
        import subprocess

        evaluator = SWEEvaluator()
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@local"], cwd=repo, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "test"], cwd=repo, capture_output=True
        )
        (repo / "solution.py").write_text("print('hello')\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, capture_output=True
        )
        (repo / "solution.py").write_text("print('hello world')\n")

        task = TaskSpec(
            id="t",
            name="t",
            track=TaskTrack.SWE,
            task_prompt="do thing",
            test_command="false",  # always exits 1, runs no tests
        )

        result = evaluator.evaluate(task, repo)
        assert result.success == 0.0
        assert result.error_class == ErrorClass.CRASH


# ---------------------------------------------------------------------------
# Judge prompt injection: $variable in task/diff
# ---------------------------------------------------------------------------


class TestJudgePromptInjection:
    """string.Template should not substitute $vars from user content."""

    def test_dollar_sign_in_diff_does_not_inject(self):
        """A diff containing $task_description should not be expanded."""
        judge = FrozenJudge(api_key=None)
        task = TaskSpec(
            id="t",
            name="t",
            track=TaskTrack.OPEN_ENDED,
            task_prompt="Write a function",
        )
        # Diff contains a template variable reference that would
        # inject the task description if not properly escaped.
        diff = 'diff --git a/f.py b/f.py\n+$task_description injected\n'
        prompt = judge.get_prompt(task, diff, DEFAULT_RUBRIC)
        # The literal "$task_description" should NOT appear expanded
        # in the prompt. The escaped version "$$task_description" is
        # rendered back to "$task_description" by safe_substitute, so
        # we check that the task description text ("Write a function")
        # does not appear duplicated in the diff section.
        assert prompt.count("Write a function") == 1

    def test_dollar_sign_in_task_description_does_not_inject(self):
        """A task description containing $diff should not be expanded."""
        judge = FrozenJudge(api_key=None)
        task = TaskSpec(
            id="t",
            name="t",
            track=TaskTrack.OPEN_ENDED,
            task_prompt="Fix $diff and also $score_keys",
        )
        diff = "diff --git a/f.py b/f.py\n+pass\n"
        prompt = judge.get_prompt(task, diff, DEFAULT_RUBRIC)
        # The literal $diff and $score_keys should be preserved as-is
        # in the task description, not expanded.
        assert "$diff" in prompt
        assert "$score_keys" in prompt


# ---------------------------------------------------------------------------
# Judge score clamping
# ---------------------------------------------------------------------------


class TestScoreClamping:
    """Scores above max_score should be clamped, not over-counted."""

    def test_clamp_above_max(self):
        rubric = Rubric(
            criteria=[RubricCriterion(name="c", description="test", max_score=5, weight=1.0)],
        )
        # Judge returns 8 for a 0-5 criterion
        success = rubric.score_to_success({"c": 8})
        assert success == 1.0  # clamped to 5/5

    def test_clamp_below_zero(self):
        rubric = Rubric(
            criteria=[RubricCriterion(name="c", description="test", max_score=5, weight=1.0)],
        )
        success = rubric.score_to_success({"c": -3})
        assert success == 0.0  # clamped to 0/5

    def test_normal_score_unchanged(self):
        rubric = Rubric(
            criteria=[RubricCriterion(name="c", description="test", max_score=5, weight=1.0)],
        )
        success = rubric.score_to_success({"c": 3})
        assert success == 0.6  # 3/5


# ---------------------------------------------------------------------------
# Path traversal sanitization
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """User-supplied identifiers must not escape the output directory."""

    def test_sanitize_id_replaces_slashes(self):
        assert sanitize_id("../etc/passwd") == ".._etc_passwd"

    def test_sanitize_id_replaces_spaces(self):
        assert sanitize_id("my run") == "my_run"

    def test_sanitize_id_keeps_safe_chars(self):
        assert sanitize_id("run-1.0_beta") == "run-1.0_beta"

    def test_sanitize_id_empty(self):
        assert sanitize_id("") == "unknown"

    def test_assert_safe_path_rejects_traversal(self, tmp_path: Path):
        base = tmp_path / "reports"
        base.mkdir()
        target = tmp_path / "etc" / "passwd"
        with pytest.raises(ValueError, match="Path traversal"):
            assert_safe_path(base, target)

    def test_assert_safe_path_accepts_inside(self, tmp_path: Path):
        base = tmp_path / "reports"
        base.mkdir()
        target = base / "report.html"
        result = assert_safe_path(base, target)
        assert str(result).startswith(str(base.resolve()))


# ---------------------------------------------------------------------------
# Container name sanitization
# ---------------------------------------------------------------------------


class TestContainerNameSanitization:
    """Container names must be safe for Docker."""

    def test_basic_cell_id(self):
        assert _sanitize_container_name("opencode__claude__task1__0") == (
            "harnessbench-opencode__claude__task1__0"
        )

    def test_replaces_unsafe_chars(self):
        name = _sanitize_container_name("cell with/slashes")
        assert "/" not in name
        assert " " not in name
        assert name.startswith("harnessbench-")

    def test_starts_with_alphanumeric(self):
        name = _sanitize_container_name("_underscore_prefix")
        assert name[0].isalnum()


# ---------------------------------------------------------------------------
# Orchestrator progress lock
# ---------------------------------------------------------------------------


class TestProgressLock:
    """Progress mutations should be protected by a lock."""

    def test_progress_lock_exists(self):
        from heval.orchestrator.engine import Orchestrator

        harness = HarnessSpec(name="h", adapter="opencode", observability_tier="full")
        model = ModelSpec(name="m", provider="anthropic", api_key_env="KEY")
        config = RunConfig(
            name="test",
            harnesses=[harness],
            models=[model],
            tasks=["t"],
            task_library_path="./tasks",
        )
        from heval.orchestrator.results_store import ResultsStore

        store = ResultsStore(":memory:")
        orch = Orchestrator(config, store)
        assert hasattr(orch, "_progress_lock")
        assert isinstance(orch._progress_lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# RunConfig validation
# ---------------------------------------------------------------------------


class TestRunConfigValidation:
    """RunConfig should reject unsafe identifiers."""

    def test_rejects_run_name_with_slash(self):
        with pytest.raises(ValueError, match="invalid characters"):
            RunConfig(
                name="../bad",
                harnesses=[HarnessSpec(name="h", adapter="a")],
                models=[ModelSpec(name="m", provider="anthropic", api_key_env="K")],
                tasks=["t"],
                task_library_path="./tasks",
            )

    def test_rejects_harness_name_with_space(self):
        with pytest.raises(ValueError, match="invalid characters"):
            HarnessSpec(name="bad harness", adapter="a")

    def test_rejects_model_name_with_slash(self):
        with pytest.raises(ValueError, match="invalid characters"):
            ModelSpec(name="bad/model", provider="anthropic", api_key_env="K")


# ---------------------------------------------------------------------------
# get_pricing_strict warns on unknown models
# ---------------------------------------------------------------------------


class TestPricingStrict:
    """Unknown models should produce a warning, not silent $0."""

    def test_warns_on_unknown_model(self, caplog: pytest.LogCaptureFixture):
        import logging

        from heval.gateway.models import get_pricing_strict

        with caplog.at_level(logging.WARNING):
            pricing = get_pricing_strict("nonexistent-model-xyz")
        assert pricing.input_per_million == 0
        assert any("nonexistent-model-xyz" in r.message for r in caplog.records)

    def test_known_model_no_warning(self, caplog: pytest.LogCaptureFixture):
        import logging

        from heval.gateway.models import get_pricing_strict

        with caplog.at_level(logging.WARNING):
            pricing = get_pricing_strict("claude-sonnet-4-20250514")
        assert pricing.input_per_million > 0
        assert not any("claude-sonnet-4" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# CallStore.delete_by_trace
# ---------------------------------------------------------------------------


class TestDeleteByTrace:
    """delete_by_trace should remove calls for a trace ID."""

    def test_delete_by_trace_removes_calls(self, tmp_path: Path):
        from heval.gateway.models import (
            CapturedCall,
            CostBreakdown,
            Provider,
            TokenUsage,
        )
        from heval.gateway.store import CallStore

        store = CallStore(tmp_path / "test.db")
        call = CapturedCall(
            id="call-1",
            trace_id="trace-1",
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            method="POST",
            path="/v1/messages",
            request_headers={},
            request_body=None,
            response_status=200,
            response_headers={},
            response_body=None,
            usage=TokenUsage(),
            cost=CostBreakdown(),
            latency_ms=100.0,
            is_streaming=False,
        )
        store.save(call)
        assert len(store.get_by_trace("trace-1")) == 1

        store.delete_by_trace("trace-1")
        assert len(store.get_by_trace("trace-1")) == 0
