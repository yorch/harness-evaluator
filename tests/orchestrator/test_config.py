"""Tests for orchestrator config and matrix building."""

from __future__ import annotations

import pytest
import yaml

from heval.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunConfig,
    TaskLibrary,
    TaskTrack,
)


@pytest.fixture
def tmp_task_dir(tmp_path):
    """Create a temporary task library directory with sample tasks."""
    task1 = {
        "tasks": [
            {
                "id": "test-task-1",
                "name": "Test Task 1",
                "track": "swe",
                "difficulty": "easy",
                "description": "A test task",
                "task_prompt": "Fix the bug",
                "test_command": "pytest tests/",
            }
        ]
    }
    task2 = {
        "tasks": [
            {
                "id": "test-task-2",
                "name": "Test Task 2",
                "track": "open_ended",
                "difficulty": "medium",
                "description": "An open-ended task",
                "task_prompt": "Build a feature",
            }
        ]
    }
    (tmp_path / "task1.yaml").write_text(yaml.dump(task1))
    (tmp_path / "task2.yaml").write_text(yaml.dump(task2))
    return tmp_path


@pytest.fixture
def sample_config(tmp_task_dir):
    """Create a sample RunConfig."""
    return RunConfig(
        name="test-run",
        harnesses=[
            HarnessSpec(name="opencode", adapter="opencode", observability_tier="full"),
            HarnessSpec(name="claude-code", adapter="claude_code", observability_tier="partial"),
        ],
        models=[
            ModelSpec(
                name="claude-sonnet-4-20250514",
                provider="anthropic",
                api_key_env="ANTHROPIC_API_KEY",
            ),
            ModelSpec(
                name="gpt-4o",
                provider="openai",
                api_key_env="OPENAI_API_KEY",
            ),
        ],
        tasks=["*"],
        task_library_path=str(tmp_task_dir),
        repeats=3,
    )


class TestTaskLibrary:
    def test_from_yaml(self, tmp_path):
        data = {
            "tasks": [
                {
                    "id": "t1",
                    "name": "Task 1",
                    "track": "swe",
                    "task_prompt": "Do thing",
                }
            ]
        }
        yaml_file = tmp_path / "tasks.yaml"
        yaml_file.write_text(yaml.dump(data))
        lib = TaskLibrary.from_yaml(yaml_file)
        assert len(lib.tasks) == 1
        assert lib.tasks[0].id == "t1"
        assert lib.tasks[0].track == TaskTrack.SWE

    def test_from_directory(self, tmp_task_dir):
        lib = TaskLibrary.from_directory(tmp_task_dir)
        assert len(lib.tasks) == 2
        ids = {t.id for t in lib.tasks}
        assert ids == {"test-task-1", "test-task-2"}


class TestRunConfig:
    def test_build_matrix(self, sample_config):
        cells = sample_config.build_matrix()
        # 2 harnesses × 2 models × 2 tasks × 3 repeats = 24
        assert len(cells) == 24

    def test_matrix_cell_ids_unique(self, sample_config):
        cells = sample_config.build_matrix()
        ids = [c.cell_id for c in cells]
        assert len(ids) == len(set(ids))

    def test_expand_tasks_all(self, sample_config):
        tasks = sample_config.expand_tasks()
        assert len(tasks) == 2

    def test_expand_tasks_specific(self, tmp_task_dir):
        config = RunConfig(
            name="test",
            harnesses=[HarnessSpec(name="h", adapter="a")],
            models=[ModelSpec(name="m", provider="anthropic", api_key_env="X")],
            tasks=["test-task-1"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        tasks = config.expand_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == "test-task-1"

    def test_from_yaml(self, tmp_task_dir):
        config_data = {
            "name": "yaml-test",
            "harnesses": [{"name": "h", "adapter": "a"}],
            "models": [{"name": "m", "provider": "anthropic", "api_key_env": "X"}],
            "tasks": ["*"],
            "task_library_path": str(tmp_task_dir),
            "repeats": 2,
        }
        config_file = tmp_task_dir / "config.yaml"
        config_file.write_text(yaml.dump(config_data))
        cfg = RunConfig.from_yaml(config_file)
        assert cfg.name == "yaml-test"
        assert cfg.repeats == 2
        assert len(cfg.harnesses) == 1


class TestRunCell:
    def test_cell_id_format(self, sample_config):
        cells = sample_config.build_matrix()
        cell = cells[0]
        # Format: harness__model__task__rN
        assert "__" in cell.cell_id
        assert cell.cell_id.startswith(cell.harness.name + "__")
        assert f"__r{cell.repeat}" in cell.cell_id
