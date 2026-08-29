"""SWE-bench-style evaluator: hidden tests, partial credit, error classification.

Evaluates a harness's output against hidden tests with:
  - Pass/fail with partial credit (fraction of tests passing)
  - Error classification (overfit, timeout, refusal, wrong-approach, partial, success)
  - Diff capture for analysis
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from heval.orchestrator.config import TaskSpec


class ErrorClass(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    OVERFIT = "overfit"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    WRONG_APPROACH = "wrong_approach"
    CRASH = "crash"
    NO_CHANGE = "no_change"


@dataclass
class EvaluationResult:
    """Result of evaluating a single run against hidden tests."""

    exit_class: str  # "pass" or "fail"
    success: float  # 0.0 to 1.0 (partial credit)
    error_class: ErrorClass
    error_message: str = ""
    test_output: str = ""
    tests_passed: int = 0
    tests_total: int = 0
    test_results: list[dict[str, Any]] = field(default_factory=list)
    diff: str = ""


class SWEEvaluator:
    """Evaluates SWE-bench-style tasks using hidden tests."""

    def evaluate(
        self,
        task: TaskSpec,
        workdir: str | Path,
        timeout: int | None = None,
    ) -> EvaluationResult:
        """Evaluate the harness's output in workdir against the task's hidden tests.

        Args:
            task: The task specification with test_command and test_patch.
            workdir: Directory where the harness made its changes.
            timeout: Test execution timeout in seconds.

        Returns:
            EvaluationResult with pass/fail, partial credit, and error class.
        """
        workdir = Path(workdir)
        timeout = timeout or task.timeout_seconds

        # Check if any changes were made
        diff = self._get_diff(workdir)
        if not diff.strip():
            return EvaluationResult(
                exit_class="fail",
                success=0.0,
                error_class=ErrorClass.NO_CHANGE,
                error_message="No changes were made to the repository",
                diff="",
            )

        # Apply hidden test patch if provided
        if task.test_patch:
            patch_result = self._apply_patch(workdir, task.test_patch)
            if not patch_result:
                return EvaluationResult(
                    exit_class="fail",
                    success=0.0,
                    error_class=ErrorClass.CRASH,
                    error_message="Failed to apply hidden test patch",
                    diff=diff,
                )

        # Run tests
        if not task.test_command:
            return EvaluationResult(
                exit_class="fail",
                success=0.0,
                error_class=ErrorClass.CRASH,
                error_message="No test command specified for task",
                diff=diff,
            )

        test_output, returncode, timed_out = self._run_tests(
            workdir, task.test_command, timeout
        )

        if timed_out:
            return EvaluationResult(
                exit_class="fail",
                success=0.0,
                error_class=ErrorClass.TIMEOUT,
                error_message=f"Tests timed out after {timeout}s",
                test_output=test_output,
                diff=diff,
            )

        # Parse test results
        tests_passed, tests_total, test_results = self._parse_test_output(
            test_output, returncode
        )

        # Calculate partial credit
        if tests_total == 0:
            # No tests were parsed. A returncode of 0 with zero tests
            # usually means the test runner collected nothing (e.g. a
            # misconfigured pytest). Score as 0 with NO_TESTS rather
            # than a perfect pass, which would silently inflate scores.
            if returncode == 0:
                return EvaluationResult(
                    exit_class="fail",
                    success=0.0,
                    error_class=ErrorClass.CRASH,
                    error_message="Test command ran but collected 0 tests",
                    test_output=test_output,
                    tests_passed=0,
                    tests_total=0,
                    test_results=test_results,
                    diff=diff,
                )
            success = 0.0
        else:
            success = tests_passed / tests_total

        # Determine error class
        error_message = ""
        if success == 1.0:
            error_class = ErrorClass.SUCCESS
            exit_class = "pass"
        elif success == 0.0:
            # Distinguish crash from wrong approach
            if returncode != 0 and tests_total == 0:
                # No tests were parsed and non-zero exit — likely a crash
                error_class = ErrorClass.CRASH
                error_message = "Test runner crashed or produced no parseable results"
            elif self._looks_like_overfit(test_output, diff):
                error_class = ErrorClass.OVERFIT
            else:
                error_class = ErrorClass.WRONG_APPROACH
            exit_class = "fail"
        else:
            error_class = ErrorClass.PARTIAL
            exit_class = "fail"

        # Check for refusal patterns in diff
        if self._looks_like_refusal(diff):
            error_class = ErrorClass.REFUSAL
            exit_class = "fail"
            success = 0.0

        return EvaluationResult(
            exit_class=exit_class,
            success=success,
            error_class=error_class,
            error_message=error_message,
            test_output=test_output,
            tests_passed=tests_passed,
            tests_total=tests_total,
            test_results=test_results,
            diff=diff,
        )

    def _get_diff(self, workdir: Path) -> str:
        """Get the git diff of changes made by the harness.

        Tries multiple strategies:
        1. `git diff HEAD` — uncommitted changes (staged + unstaged)
        2. `git diff HEAD~1` — changes in the last commit
        3. Check for untracked files via `git status`

        This handles harnesses that commit, stage, or just modify files.
        """
        try:
            # Check for uncommitted changes (staged + unstaged) against HEAD
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=workdir,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
            if result.stdout.strip():
                return result.stdout

            # Fall back to last commit's changes
            result = subprocess.run(
                ["git", "diff", "HEAD~1"],
                cwd=workdir,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
            if result.stdout.strip():
                return result.stdout

            # Check for untracked files and generate real content diffs
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workdir,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
            if status_result.stdout.strip():
                # Parse porcelain output and generate real diffs for
                # untracked files (?? prefix) that git diff HEAD misses.
                diffs: list[str] = []
                for line in status_result.stdout.strip().splitlines():
                    if not line.strip():
                        continue
                    # Porcelain format: "XY <path>" (2-char status + space + path)
                    if line[:2] == "??":
                        file_path = line[3:]
                        full_path = workdir / file_path
                        if full_path.is_file():
                            diff_result = subprocess.run(
                                [
                                    "git", "diff", "--no-index",
                                    "/dev/null", str(full_path),
                                ],
                                cwd=workdir,
                                capture_output=True,
                                text=True,
                                errors="replace",
                                timeout=10,
                            )
                            # --no-index exits 1 when files differ (expected)
                            if diff_result.stdout.strip():
                                diffs.append(diff_result.stdout)

                if diffs:
                    return "\n".join(diffs)

                # No untracked files with content diffs; fall back to status
                return f"--- untracked changes ---\n{status_result.stdout}"

            return ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _apply_patch(self, workdir: Path, patch: str) -> bool:
        """Apply a test patch to the workdir."""
        try:
            result = subprocess.run(
                ["git", "apply", "-"],
                cwd=workdir,
                input=patch,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _run_tests(
        self, workdir: Path, command: str, timeout: int
    ) -> tuple[str, int, bool]:
        """Run the test command and return (output, returncode, timed_out).

        Uses ``shlex.split`` to parse the command into an argument list
        instead of ``shell=True``, avoiding shell injection from task
        YAML. Commands that require shell features (pipes, redirects,
        variable expansion) should be wrapped in ``bash -c "..."`` in
        the task definition.
        """
        try:
            result = subprocess.run(
                shlex.split(command),
                cwd=workdir,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            return output, result.returncode, False
        except subprocess.TimeoutExpired as e:
            raw_out = e.stdout or ""
            raw_err = e.stderr or ""
            if isinstance(raw_out, bytes):
                raw_out = raw_out.decode("utf-8", errors="replace")
            if isinstance(raw_err, bytes):
                raw_err = raw_err.decode("utf-8", errors="replace")
            timeout_output: str = raw_out + raw_err
            return timeout_output, -1, True

    def _parse_test_output(
        self, output: str, returncode: int
    ) -> tuple[int, int, list[dict[str, Any]]]:
        """Parse test output to count passed/total tests.

        Supports pytest, bun test, and unittest output formats.
        Returns (passed, total, detail_list).
        """
        results: list[dict[str, Any]] = []

        # Try pytest format: "X passed, Y failed, Z errors" in any order
        passed = 0
        failed = 0
        errors = 0
        found_any = False

        pass_match = re.search(r"(\d+) passed", output)
        if pass_match:
            passed = int(pass_match.group(1))
            found_any = True

        fail_match = re.search(r"(\d+) failed", output)
        if fail_match:
            failed = int(fail_match.group(1))
            found_any = True

        error_match = re.search(r"(\d+) errors?", output)
        if error_match:
            errors = int(error_match.group(1))
            found_any = True

        if found_any:
            total = passed + failed + errors
            return passed, total, results

        # Try bun test format: "X pass", "Y fail", "Z skip" (word-boundary so
        # it does not collide with pytest's "passed"/"failed").
        bun_pass = re.search(r"(\d+) pass\b", output)
        bun_fail = re.search(r"(\d+) fail\b", output)
        if bun_pass or bun_fail:
            passed = int(bun_pass.group(1)) if bun_pass else 0
            failed = int(bun_fail.group(1)) if bun_fail else 0
            total = passed + failed
            if total > 0:
                return passed, total, results

        # Try unittest format: "Ran X tests in Ys" + "OK" or "FAILED"
        unittest_match = re.search(r"Ran (\d+) tests", output)
        if unittest_match:
            total = int(unittest_match.group(1))
            if "OK" in output and "FAILED" not in output:
                return total, total, results
            # Count failures + errors from the "FAILED (...)" summary line, or
            # fall back to counting individual FAIL:/ERROR: lines.
            failures_match = re.search(r"failures=(\d+)", output)
            errors_match = re.search(r"errors=(\d+)", output)
            if failures_match or errors_match:
                failed = int(failures_match.group(1)) if failures_match else 0
                errors = int(errors_match.group(1)) if errors_match else 0
                bad = failed + errors
            else:
                bad = len(re.findall(r"^(?:FAIL|ERROR):", output, re.MULTILINE))
            return max(total - bad, 0), total, results

        # Fallback: no parseable test output.
        # Previously, returncode=0 returned (1, 1) — a perfect pass —
        # which meant a test command like `true` (that runs zero tests)
        # would score 100%. Now we return (0, 0) so the caller can
        # distinguish "no tests collected" from "all tests passed".
        if returncode == 0:
            return 0, 0, results
        # Non-zero exit with no parseable test output: return (0, 0) so
        # the caller can classify this as CRASH (tests_total == 0 with
        # non-zero returncode). Returning (0, 1) here would make the
        # CRASH branch unreachable and misclassify crashes as
        # WRONG_APPROACH or OVERFIT.
        return 0, 0, results

    def _looks_like_refusal(self, diff: str) -> bool:
        """Check if the diff looks like a natural-language refusal.

        Only considers added lines (``+``) and matches natural-language
        refusal phrases. ``raise NotImplementedError`` is intentionally NOT
        treated as a refusal — it is legitimate in abstract methods/stubs.
        """
        added = "\n".join(
            line[1:]
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        refusal_patterns = [
            r"I cannot (help|modify|change|assist)",
            r"I'm unable to",
            r"I am unable to",
            r"This is not something I can",
        ]
        return any(re.search(pattern, added, re.IGNORECASE) for pattern in refusal_patterns)

    def _looks_like_overfit(self, test_output: str, diff: str) -> bool:
        """Heuristic: check if the solution looks overfit to visible tests.

        Conservative: only flags a very short diff whose added lines directly
        reference a value that also appears in the visible test output (a
        strong signal of hardcoding to specific test cases), rather than any
        ``if x == 0`` / ``return 0`` which occur in ordinary correct code.
        """
        added_lines = [
            line[1:].strip()
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        # Only suspicious for very small diffs.
        if not added_lines or len(added_lines) >= 6:
            return False
        # Look for literal numeric/string constants in added code that also
        # appear verbatim in the visible test output.
        literal_re = re.compile(r"(?:==|return)\s*['\"]?([A-Za-z0-9_]{2,})['\"]?")
        for line in added_lines:
            for m in literal_re.finditer(line):
                literal = m.group(1)
                if literal and literal in test_output:
                    return True
        return False
