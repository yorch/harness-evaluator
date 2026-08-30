"""Tests for the task library: YAML validation and hidden patch integrity.

These tests load every YAML file under ``tasks/`` via
``TaskLibrary.from_directory()`` and assert that SWE tasks have valid,
applyable hidden test patches and that their referenced repos exist.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness_evaluator.orchestrator.config import TaskLibrary, TaskTrack

# The tasks directory lives at the repository root, two levels up from
# this test file (tests/test_task_library.py -> repo root).
TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"


@pytest.fixture(scope="module")
def task_library() -> TaskLibrary:
    """Load the full task library from the tasks/ directory."""
    return TaskLibrary.from_directory(TASKS_DIR)


def _swe_tasks(library: TaskLibrary) -> list:
    """Return only the SWE-track tasks from the library."""
    return [t for t in library.tasks if t.track == TaskTrack.SWE]


class TestTaskLibraryLoading:
    """Verify every YAML file in tasks/ loads cleanly."""

    def test_all_yaml_files_load(self, task_library: TaskLibrary) -> None:
        """Every YAML file in tasks/ should load, one task per file."""
        yaml_files = sorted(TASKS_DIR.glob("*.yaml"))
        assert len(yaml_files) > 0, "No YAML task files found in tasks/"
        # Each task file currently defines exactly one task, so the loaded
        # task count must equal the number of YAML files (catches a silently
        # dropped/unindexed file).
        assert len(task_library.tasks) == len(yaml_files), (
            f"Loaded {len(task_library.tasks)} tasks from {len(yaml_files)} files"
        )

    def test_track_and_language_distribution(self, task_library: TaskLibrary) -> None:
        """The curated mix has both tracks and both languages represented."""
        tracks = {t.track for t in task_library.tasks}
        assert TaskTrack.SWE in tracks and TaskTrack.OPEN_ENDED in tracks
        languages = {t.metadata.get("language") for t in task_library.tasks}
        assert "python" in languages, "No Python tasks found"
        assert "typescript" in languages, "No TypeScript tasks found"

    def test_typescript_tasks_use_bun(self, task_library: TaskLibrary) -> None:
        """TypeScript tasks must use `bun test` as their test command."""
        ts_tasks = [
            t for t in task_library.tasks
            if t.metadata.get("language") == "typescript" and t.test_command
        ]
        assert ts_tasks, "No TypeScript tasks with a test_command found"
        for t in ts_tasks:
            assert "bun" in (t.test_command or ""), (
                f"TypeScript task {t.id} test_command is not bun-based: {t.test_command}"
            )

    def test_task_ids_unique(self, task_library: TaskLibrary) -> None:
        """All task IDs must be unique."""
        ids = [t.id for t in task_library.tasks]
        assert len(ids) == len(set(ids)), f"Duplicate task IDs: {ids}"

    def test_swe_tasks_present(self, task_library: TaskLibrary) -> None:
        """There should be at least one SWE task to validate."""
        assert len(_swe_tasks(task_library)) > 0, "No SWE tasks found"


class TestSWETaskPatchValidation:
    """Validate the test_patch field of every SWE task."""

    def test_test_patch_non_empty_and_has_newlines(
        self, task_library: TaskLibrary
    ) -> None:
        """Each SWE task's test_patch must be non-empty and contain newlines.

        A patch folded into a single line by YAML (e.g. via a bad flow
        style or a missing literal block indicator) would not contain
        newlines, so we check for them explicitly.
        """
        for task in _swe_tasks(task_library):
            assert task.test_patch is not None, (
                f"Task {task.id} has a null test_patch"
            )
            assert task.test_patch.strip() != "", (
                f"Task {task.id} has an empty test_patch"
            )
            assert "\n" in task.test_patch, (
                f"Task {task.id} test_patch has no newlines "
                "(possibly folded by YAML)"
            )

    def test_test_patch_has_unified_diff_header(
        self, task_library: TaskLibrary
    ) -> None:
        """Each SWE task's test_patch must start with a standard diff header."""
        for task in _swe_tasks(task_library):
            assert task.test_patch is not None
            stripped = task.test_patch.lstrip()
            assert stripped.startswith("diff --git") or stripped.startswith(
                "--- "
            ), (
                f"Task {task.id} test_patch does not start with a "
                f"unified diff header (got: {stripped[:40]!r})"
            )

    def test_referenced_repo_exists(self, task_library: TaskLibrary) -> None:
        """Each SWE task's referenced repo must exist at tasks/repos/<id>/."""
        for task in _swe_tasks(task_library):
            repo_path = TASKS_DIR / "repos" / task.id
            assert repo_path.is_dir(), (
                f"Task {task.id} references missing repo at {repo_path}"
            )


def _added_files_from_patch(patch: str) -> list[str]:
    """Extract the list of added file paths (b/ side) from a unified diff."""
    files: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[len("+++ b/"):])
        elif line.startswith("+++ "):
            files.append(line[len("+++ "):])
    return files


class TestSWETaskHiddenPatchApplies:
    """Verify each SWE task's hidden test_patch applies cleanly to its repo."""

    @pytest.fixture
    def git_available(self) -> None:
        """Skip the whole class if git is not installed."""
        if shutil.which("git") is None:
            pytest.skip("git is not installed")

    def test_hidden_patch_applies_cleanly(
        self, task_library: TaskLibrary, git_available: None, tmp_path: Path
    ) -> None:
        """For each SWE task, copy the repo to a temp dir and apply test_patch."""
        for task in _swe_tasks(task_library):
            self._apply_and_verify(task, tmp_path)

    def _apply_and_verify(self, task, tmp_path: Path) -> None:
        """Copy repo, apply patch, assert success and expected test file."""
        repo_src = TASKS_DIR / "repos" / task.id
        assert repo_src.is_dir(), f"Repo missing for task {task.id}"

        # Copy the repo (including .git) to an isolated temp directory.
        repo_dst = tmp_path / f"{task.id}_apply_test"
        shutil.copytree(repo_src, repo_dst)

        # Write the test_patch to a file so `git apply` can consume it.
        assert task.test_patch is not None
        patch_file = tmp_path / f"{task.id}.patch"
        patch_file.write_text(task.test_patch)

        # Apply the patch against the copied repo.
        result = subprocess.run(
            ["git", "apply", str(patch_file)],
            cwd=repo_dst,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"git apply failed for task {task.id}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The patch should have created the expected hidden test file.
        added_files = _added_files_from_patch(task.test_patch)
        assert added_files, (
            f"Task {task.id} test_patch adds no files "
            "(no '+++ b/...' header found)"
        )
        for rel_path in added_files:
            created = repo_dst / rel_path
            assert created.is_file(), (
                f"Task {task.id}: expected test file {rel_path} "
                f"not found after applying patch"
            )

        # Clean up the copy for the next iteration.
        shutil.rmtree(repo_dst, ignore_errors=True)
