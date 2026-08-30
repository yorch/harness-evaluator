"""Tests for the SWE evaluator."""

from __future__ import annotations

import subprocess

import pytest

from harnessbench.evaluator.swe import ErrorClass, SWEEvaluator
from harnessbench.orchestrator.config import TaskSpec, TaskTrack


@pytest.fixture
def swe_evaluator():
    return SWEEvaluator()


@pytest.fixture
def mock_repo(tmp_path):
    """Create a mock git repo with a buggy file."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Create a buggy source file
    (repo / "src").mkdir()
    (repo / "src" / "utils.py").write_text(
        "def range_sum(n):\n"
        "    total = 0\n"
        "    for i in range(1, n):  # Bug: should be range(1, n+1)\n"
        "        total += i\n"
        "    return total\n"
    )

    # Create __init__.py
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")

    # Init git
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)

    return repo


@pytest.fixture
def swe_task(tmp_path):
    """Create a task that uses a simple Python test script."""
    # Create a test script that will be the test command
    test_script = tmp_path / "run_tests.py"
    test_script.write_text(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from src.utils import range_sum\n\n"
        "passed = 0\n"
        "total = 0\n\n"
        "total += 1\n"
        "if range_sum(1) == 1:\n"
        "    passed += 1\n"
        "total += 1\n"
        "if range_sum(5) == 15:\n"
        "    passed += 1\n"
        "total += 1\n"
        "if range_sum(0) == 0:\n"
        "    passed += 1\n\n"
        "print(f'{passed} passed, {total - passed} failed')\n"
        "if passed < total:\n"
        "    sys.exit(1)\n"
    )

    return TaskSpec(
        id="test-task",
        name="Test Task",
        track=TaskTrack.SWE,
        description="Fix the bug",
        task_prompt="Fix the off-by-one bug in range_sum",
        test_command=f"python {test_script}",
    )


class TestSWEEvaluator:
    def test_no_change_detected(self, swe_evaluator, mock_repo, swe_task):
        """Test that no changes result in NO_CHANGE error class."""
        result = swe_evaluator.evaluate(swe_task, mock_repo)
        assert result.exit_class == "fail"
        assert result.error_class == ErrorClass.NO_CHANGE
        assert result.success == 0.0

    def test_successful_fix(self, swe_evaluator, mock_repo, swe_task):
        """Test that a correct fix passes."""
        # Fix the bug
        (mock_repo / "src" / "utils.py").write_text(
            "def range_sum(n):\n"
            "    total = 0\n"
            "    for i in range(1, n + 1):  # Fixed\n"
            "        total += i\n"
            "    return total\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix"], cwd=mock_repo, capture_output=True)

        result = swe_evaluator.evaluate(swe_task, mock_repo)
        assert result.exit_class == "pass"
        assert result.error_class == ErrorClass.SUCCESS
        assert result.success == 1.0

    def test_wrong_fix(self, swe_evaluator, mock_repo, swe_task):
        """Test that an incorrect fix fails."""
        (mock_repo / "src" / "utils.py").write_text(
            "def range_sum(n):\n"
            "    return n  # Wrong: just returns n\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix"], cwd=mock_repo, capture_output=True)

        result = swe_evaluator.evaluate(swe_task, mock_repo)
        assert result.exit_class == "fail"
        assert result.success < 1.0

    def test_diff_captured(self, swe_evaluator, mock_repo, swe_task):
        """Test that the diff is captured."""
        (mock_repo / "src" / "utils.py").write_text("# changed\n")
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "change"], cwd=mock_repo, capture_output=True)

        result = swe_evaluator.evaluate(swe_task, mock_repo)
        assert result.diff  # Non-empty diff
        assert "changed" in result.diff

    def test_refusal_detected(self, swe_evaluator, mock_repo, swe_task):
        """Test that refusal patterns are detected."""
        (mock_repo / "src" / "utils.py").write_text(
            "# I cannot modify this file\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=mock_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "refusal"], cwd=mock_repo, capture_output=True)

        result = swe_evaluator.evaluate(swe_task, mock_repo)
        assert result.error_class == ErrorClass.REFUSAL
        assert result.success == 0.0

    def test_untracked_file_diff_returns_real_content(
        self, swe_evaluator, mock_repo, swe_task
    ):
        """Test that untracked files produce a real content diff, not status."""
        # Create a new untracked file (not staged or committed)
        (mock_repo / "src" / "new_module.py").write_text(
            "def new_function():\n"
            "    return 42\n"
        )

        diff = swe_evaluator._get_diff(mock_repo)

        # The diff should contain real file content, not a status pseudo-diff
        assert diff
        assert "--- untracked changes ---" not in diff
        assert "new_function" in diff
        assert "def new_function" in diff
        assert "return 42" in diff
        # Should look like a proper diff with diff headers
        assert "diff --git" in diff or "---" in diff


class TestSWEEvaluatorTestParsing:
    def test_parse_pytest_output_pass(self, swe_evaluator):
        output = "===== 3 passed in 0.5s ====="
        passed, total, _ = swe_evaluator._parse_test_output(output, 0)
        assert passed == 3
        assert total == 3

    def test_parse_pytest_output_mixed(self, swe_evaluator):
        output = "===== 2 passed, 1 failed in 0.5s ====="
        passed, total, _ = swe_evaluator._parse_test_output(output, 1)
        assert passed == 2
        assert total == 3

    def test_parse_pytest_output_with_errors(self, swe_evaluator):
        output = "===== 2 passed, 1 failed, 1 error in 0.5s ====="
        passed, total, _ = swe_evaluator._parse_test_output(output, 1)
        assert passed == 2
        assert total == 4

    def test_parse_unittest_output_ok(self, swe_evaluator):
        output = "Ran 3 tests in 0.1s\nOK"
        passed, total, _ = swe_evaluator._parse_test_output(output, 0)
        assert passed == 3
        assert total == 3

    def test_parse_unittest_output_failed(self, swe_evaluator):
        output = "Ran 3 tests in 0.1s\nFAILED (failures=1)"
        passed, total, _ = swe_evaluator._parse_test_output(output, 1)
        assert passed == 2
        assert total == 3

    def test_parse_simple_output(self, swe_evaluator):
        """Test parsing simple 'X passed, Y failed' format."""
        output = "2 passed, 1 failed"
        passed, total, _ = swe_evaluator._parse_test_output(output, 1)
        assert passed == 2
        assert total == 3
