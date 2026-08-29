"""Tests for the open-ended evaluator."""

from __future__ import annotations

import subprocess

import pytest

from heval.evaluator.open_ended import (
    DEFAULT_RUBRIC,
    CalibrationSet,
    FrozenJudge,
    JudgeResult,
    JudgeVersion,
    OpenEndedEvaluator,
    Rubric,
    RubricCriterion,
    StructuralChecker,
)
from heval.orchestrator.config import TaskSpec, TaskTrack


@pytest.fixture
def mock_repo(tmp_path):
    """Create a mock git repo with a submission."""
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "src").mkdir()
    (repo / "src" / "cache.py").write_text(
        "def cached(max_size=128):\n"
        "    def decorator(func):\n"
        "        cache = {}\n"
        "        def wrapper(*args, **kwargs):\n"
        "            key = (args, tuple(kwargs.items()))\n"
        "            if key not in cache:\n"
        "                cache[key] = func(*args, **kwargs)\n"
        "            return cache[key]\n"
        "        return wrapper\n"
        "    return decorator\n"
    )
    (repo / "src" / "__init__.py").write_text("")

    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_cache.py").write_text(
        "from src.cache import cached\n\n"
        "def test_basic():\n"
        "    call_count = 0\n"
        "    @cached()\n"
        "    def func(x):\n"
        "        nonlocal call_count\n"
        "        call_count += 1\n"
        "        return x * 2\n"
        "    assert func(5) == 10\n"
        "    assert func(5) == 10\n"
        "    assert call_count == 1\n"
    )

    (repo / "README.md").write_text("# Caching Decorator\n\nA simple LRU cache.")

    # Init git
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)

    # Make a change
    (repo / "src" / "cache.py").write_text(
        "# Improved version\n"
        + (repo / "src" / "cache.py").read_text()
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "improvement"], cwd=repo, capture_output=True)

    return repo


@pytest.fixture
def open_ended_task():
    return TaskSpec(
        id="open-design-001",
        name="Design a caching decorator",
        track=TaskTrack.OPEN_ENDED,
        description="Implement a @cached decorator with LRU eviction and thread safety",
        task_prompt="Implement a @cached decorator",
        expected_files=["src/cache.py", "tests/test_cache.py", "README.md"],
    )


class TestRubric:
    def test_default_rubric_has_criteria(self):
        assert len(DEFAULT_RUBRIC.criteria) == 5
        assert DEFAULT_RUBRIC.total_weight > 0

    def test_score_to_success_all_max(self):
        rubric = Rubric(
            criteria=[
                RubricCriterion(name="a", description="test", weight=1.0, max_score=5),
                RubricCriterion(name="b", description="test", weight=2.0, max_score=5),
            ]
        )
        scores = {"a": 5, "b": 5}
        success = rubric.score_to_success(scores)
        assert success == 1.0

    def test_score_to_success_all_zero(self):
        rubric = Rubric(
            criteria=[
                RubricCriterion(name="a", description="test", weight=1.0, max_score=5),
            ]
        )
        scores = {"a": 0}
        success = rubric.score_to_success(scores)
        assert success == 0.0

    def test_score_to_success_weighted(self):
        rubric = Rubric(
            criteria=[
                RubricCriterion(name="a", description="test", weight=3.0, max_score=5),
                RubricCriterion(name="b", description="test", weight=1.0, max_score=5),
            ]
        )
        # a=5 (1.0), b=0 (0.0) → weighted: (1.0*3 + 0.0*1) / 4 = 0.75
        scores = {"a": 5, "b": 0}
        success = rubric.score_to_success(scores)
        assert success == 0.75

    def test_score_to_success_empty(self):
        rubric = Rubric(criteria=[])
        assert rubric.score_to_success({}) == 0.0


class TestStructuralChecker:
    def test_all_files_present(self, mock_repo, open_ended_task):
        checker = StructuralChecker()
        result = checker.check(open_ended_task, mock_repo)
        # All expected files exist
        file_checks = [c for c in result.checks if c["name"].startswith("file_exists:")]
        assert all(c["passed"] for c in file_checks)

    def test_missing_file(self, mock_repo, open_ended_task):
        # Remove a file
        (mock_repo / "README.md").unlink()
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "remove"], cwd=mock_repo, capture_output=True)

        checker = StructuralChecker()
        result = checker.check(open_ended_task, mock_repo)
        assert not result.passed
        readme_check = [c for c in result.checks if "README.md" in c["name"]]
        assert readme_check and not readme_check[0]["passed"]

    def test_syntax_check_passes(self, mock_repo, open_ended_task):
        checker = StructuralChecker()
        result = checker.check(open_ended_task, mock_repo)
        syntax_checks = [c for c in result.checks if c["name"].startswith("syntax:")]
        assert all(c["passed"] for c in syntax_checks)

    def test_syntax_check_fails(self, mock_repo, open_ended_task):
        # Add a file with syntax error
        (mock_repo / "src" / "bad.py").write_text("def broken(:\n  pass\n")
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "bad"], cwd=mock_repo, capture_output=True)

        checker = StructuralChecker()
        result = checker.check(open_ended_task, mock_repo)
        assert not result.passed
        bad_check = [c for c in result.checks if "bad.py" in c["name"]]
        assert bad_check and not bad_check[0]["passed"]


class TestFrozenJudge:
    def test_judge_version(self):
        judge = FrozenJudge(version=JudgeVersion.V1_0)
        assert judge.version == JudgeVersion.V1_0

    def test_get_prompt(self, open_ended_task):
        judge = FrozenJudge(version=JudgeVersion.V1_0)
        prompt = judge.get_prompt(open_ended_task, "test diff", DEFAULT_RUBRIC)
        assert "cached decorator" in prompt  # from task_prompt
        assert "correctness" in prompt
        assert "JSON" in prompt

    def test_parse_valid_response(self):
        judge = FrozenJudge()
        response = (
            '{"scores": {"correctness": 4}, '
            '"justifications": {"correctness": "Good"}, '
            '"overall_assessment": "Solid work"}'
        )
        result = judge._parse_response(response)
        assert result.scores == {"correctness": 4}
        assert result.justifications == {"correctness": "Good"}
        assert result.overall_assessment == "Solid work"
        assert result.error is None

    def test_parse_markdown_fenced_response(self):
        judge = FrozenJudge()
        response = (
            '```json\n{"scores": {"a": 3}, '
            '"justifications": {"a": "ok"}, '
            '"overall_assessment": "fine"}\n```'
        )
        result = judge._parse_response(response)
        assert result.scores == {"a": 3}
        assert result.error is None

    def test_parse_invalid_response(self):
        judge = FrozenJudge()
        result = judge._parse_response("not json at all")
        assert result.error is not None
        assert result.scores == {}

    async def test_judge_no_api_key(self, open_ended_task):
        judge = FrozenJudge(api_key=None)
        result = await judge.judge(open_ended_task, "diff", DEFAULT_RUBRIC)
        # Without API key, returns placeholder
        assert result.error is None or "No API key" in (result.error or "")


class TestCalibrationSet:
    def test_add_anchor(self):
        cal = CalibrationSet()
        cal.add_anchor("test", "diff", {"a": 5}, 1.0)
        assert len(cal.anchors) == 1
        assert cal.anchors[0]["name"] == "test"

    def test_calibrate_no_anchors(self, open_ended_task):
        cal = CalibrationSet()
        judge = FrozenJudge(api_key=None)
        result = cal.calibrate(judge, open_ended_task, DEFAULT_RUBRIC)
        assert result["num_anchors"] == 0
        assert result["mean_absolute_error"] == 0.0
        assert result["reliable"] is False  # Unknown, not reliable
        assert result["drift_detected"] is None  # Unknown

    def test_save_and_load_anchors(self, tmp_path):
        """Test that anchors can be saved to and loaded from a JSON file."""
        cal = CalibrationSet()
        cal.add_anchor(
            "anchor-1",
            "some diff content",
            {"correctness": 4, "completeness": 3},
            0.7,
            metadata={"source": "manual"},
        )
        cal.add_anchor("anchor-2", "another diff", {"correctness": 2}, 0.3)

        path = tmp_path / "calibration.json"
        cal.save_to_file(path)

        # File should exist and be valid JSON
        assert path.exists()

        # Load and verify
        loaded = CalibrationSet.load_from_file(path)
        assert len(loaded.anchors) == 2
        assert loaded.anchors[0]["name"] == "anchor-1"
        assert loaded.anchors[0]["diff"] == "some diff content"
        assert loaded.anchors[0]["expected_scores"] == {
            "correctness": 4,
            "completeness": 3,
        }
        assert loaded.anchors[0]["expected_success"] == 0.7
        assert loaded.anchors[0]["metadata"] == {"source": "manual"}
        assert loaded.anchors[1]["name"] == "anchor-2"

    def test_save_and_load_empty_anchors(self, tmp_path):
        """Test that an empty calibration set can be saved and loaded."""
        cal = CalibrationSet()
        path = tmp_path / "empty_cal.json"
        cal.save_to_file(path)

        loaded = CalibrationSet.load_from_file(path)
        assert len(loaded.anchors) == 0

    def test_save_results_after_calibrate(self, tmp_path, open_ended_task):
        """Test that calibration results can be persisted to disk."""
        cal = CalibrationSet()
        judge = FrozenJudge(api_key=None)
        cal.calibrate(judge, open_ended_task, DEFAULT_RUBRIC)

        results_path = tmp_path / "calibration_results.json"
        cal.save_results(results_path)

        assert results_path.exists()
        # Verify the file contains valid JSON with expected fields
        import json

        data = json.loads(results_path.read_text())
        assert "judge_version" in data
        assert "num_anchors" in data
        assert "mean_absolute_error" in data

    def test_save_results_without_calibrate_raises(self, tmp_path):
        """Test that save_results raises if calibrate hasn't been called."""
        cal = CalibrationSet()
        with pytest.raises(ValueError, match="No calibration results"):
            cal.save_results(tmp_path / "results.json")

    def test_load_from_nonexistent_file_raises(self, tmp_path):
        """Test that loading from a nonexistent file raises."""
        with pytest.raises(FileNotFoundError):
            CalibrationSet.load_from_file(tmp_path / "nonexistent.json")


class TestOpenEndedEvaluator:
    async def test_no_change_detected(self, mock_repo, open_ended_task):
        # Reset to clean state (no changes)
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=mock_repo, capture_output=True)

        evaluator = OpenEndedEvaluator()
        result = await evaluator.evaluate(open_ended_task, mock_repo)
        assert result.exit_class == "fail"
        assert result.error_class == "no_change"
        assert result.success == 0.0

    async def test_evaluator_with_mock_judge(self, mock_repo, open_ended_task):
        """Test evaluator with a mock judge that returns high scores."""

        class MockJudge(FrozenJudge):
            async def judge(self, task, diff, rubric, trace_id=None):
                return JudgeResult(
                    scores={c.name: 4 for c in rubric.criteria},
                    justifications={c.name: "Good" for c in rubric.criteria},
                    overall_assessment="Solid implementation",
                    judge_version="v1.0",
                )

        evaluator = OpenEndedEvaluator(judge=MockJudge())
        result = await evaluator.evaluate(open_ended_task, mock_repo)
        assert result.success > 0.5
        assert result.judge_result is not None
        assert result.judge_result.overall_assessment == "Solid implementation"

    async def test_structural_failure_caps_success(self, mock_repo, open_ended_task):
        """Test that structural failure caps success at 0.5."""

        class MockJudge(FrozenJudge):
            async def judge(self, task, diff, rubric, trace_id=None):
                return JudgeResult(
                    scores={c.name: 5 for c in rubric.criteria},
                    justifications={c.name: "Perfect" for c in rubric.criteria},
                    overall_assessment="Perfect",
                    judge_version="v1.0",
                )

        # Remove a required file to trigger structural failure
        (mock_repo / "README.md").unlink()
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "remove readme"], cwd=mock_repo, capture_output=True)

        evaluator = OpenEndedEvaluator(judge=MockJudge())
        result = await evaluator.evaluate(open_ended_task, mock_repo)
        # Even with perfect judge scores, structural failure caps at 0.5
        assert result.success <= 0.5
        assert result.error_class == "structural_failure"

    async def test_prompt_with_braces_in_diff(self, mock_repo, open_ended_task):
        """Test that diffs containing braces don't crash the judge prompt."""

        class MockJudge(FrozenJudge):
            async def judge(self, task, diff, rubric, trace_id=None):
                # Verify the prompt was generated successfully
                prompt = self.get_prompt(task, diff, rubric)
                assert "cache = {}" in prompt or "{" in prompt
                return JudgeResult(
                    scores={c.name: 3 for c in rubric.criteria},
                    justifications={c.name: "OK" for c in rubric.criteria},
                    overall_assessment="Fine",
                    judge_version="v1.0",
                )

        # Add code with braces to the diff
        (mock_repo / "src" / "cache.py").write_text(
            "cache = {}\n"
            "def cached():\n"
            "    return cache\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "braces"], cwd=mock_repo, capture_output=True)

        evaluator = OpenEndedEvaluator(judge=MockJudge())
        result = await evaluator.evaluate(open_ended_task, mock_repo)
        assert result.judge_result is not None
        assert result.judge_result.error is None
