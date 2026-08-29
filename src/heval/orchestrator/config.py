"""Task and run configuration models."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Strict allow-list for identifiers used in file paths, container names,
# and database keys. Prevents path traversal and shell injection from
# user-supplied YAML/CLI input.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class TaskTrack(StrEnum):
    SWE = "swe"
    OPEN_ENDED = "open_ended"


class TaskDifficulty(StrEnum):
    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ObservabilityTier(StrEnum):
    """Expected observability level from a harness."""

    FULL = "full"
    PARTIAL = "partial"
    MINIMAL = "minimal"


class TaskSpec(BaseModel):
    """A single task definition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    track: TaskTrack
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    description: str = ""
    repo_url: str | None = None
    repo_commit: str | None = None
    setup_script: str | None = None
    """Shell script to run after cloning the repo (install deps, etc.)."""
    task_prompt: str
    """The prompt given to the harness."""
    test_command: str | None = None
    """Command to run tests (e.g. 'pytest tests/test_foo.py')."""
    test_patch: str | None = None
    """Patch file with hidden tests to apply before evaluation."""
    expected_files: list[str] = Field(default_factory=list)
    """Files that should be modified/created by the harness."""
    timeout_seconds: int = 600
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskLibrary(BaseModel):
    """A collection of tasks loaded from YAML."""

    tasks: list[TaskSpec] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> TaskLibrary:
        """Load tasks from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        tasks = [TaskSpec(**t) for t in data.get("tasks", [])]
        return cls(tasks=tasks)

    @classmethod
    def from_directory(cls, path: str | Path) -> TaskLibrary:
        """Load all task YAML files from a directory."""
        path = Path(path)
        tasks: list[TaskSpec] = []
        for yaml_file in sorted(path.glob("*.yaml")):
            lib = cls.from_yaml(yaml_file)
            tasks.extend(lib.tasks)
        return cls(tasks=tasks)


class HarnessSpec(BaseModel):
    """A harness configuration for evaluation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    """Harness identifier (e.g. 'opencode', 'claude-code')."""
    adapter: str
    """Adapter module name."""
    config: dict[str, Any] = Field(default_factory=dict)
    """Harness-specific configuration."""
    observability_tier: ObservabilityTier = ObservabilityTier.PARTIAL
    """Expected observability level."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Harness name '{v}' contains invalid characters. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v


class ModelSpec(BaseModel):
    """A model configuration for evaluation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    """Model identifier (e.g. 'claude-sonnet-4-20250514')."""
    provider: str
    """Provider ('anthropic' or 'openai')."""
    api_key_env: str
    """Environment variable name for the API key."""
    config: dict[str, Any] = Field(default_factory=dict)
    """Model-specific configuration (temperature, max_tokens, etc.)."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Model name '{v}' contains invalid characters. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v


class RunConfig(BaseModel):
    """Full configuration for an evaluation run."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    harnesses: list[HarnessSpec]
    models: list[ModelSpec]
    tasks: list[str]
    """Task IDs to run. Use '*' for all tasks in the library."""
    task_library_path: str
    repeats: int = 5
    budget_usd: float | None = None
    """Maximum total spend in USD. None = no cap."""
    gateway_host: str = "host.docker.internal"
    """Gateway host as seen from inside Docker containers."""
    gateway_port: int = 8877
    gateway_db: str = "heval_gateway.db"
    results_db: str = "heval_results.db"
    workdir: str = "./heval_workdir"
    docker_image: str = "heval-runner:latest"
    parallel_runs: int = 1
    """Number of parallel container runs (1 = sequential)."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Run name '{v}' contains invalid characters. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v

    @classmethod
    def from_yaml(cls, path: str | Path) -> RunConfig:
        """Load run config from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def expand_tasks(self) -> list[TaskSpec]:
        """Expand task IDs to full TaskSpecs from the library."""
        lib = TaskLibrary.from_directory(self.task_library_path)
        if "*" in self.tasks:
            return lib.tasks
        task_map = {t.id: t for t in lib.tasks}
        missing = [tid for tid in self.tasks if tid not in task_map]
        if missing:
            raise ValueError(f"Unknown task IDs: {missing}. Available: {list(task_map.keys())}")
        return [task_map[tid] for tid in self.tasks]

    def build_matrix(self) -> list[RunCell]:
        """Build the full eval matrix: harness × model × task × repeat."""
        tasks = self.expand_tasks()
        cells: list[RunCell] = []
        for harness in self.harnesses:
            for model in self.models:
                for task in tasks:
                    for repeat in range(self.repeats):
                        cells.append(
                            RunCell(
                                run_name=self.name,
                                harness=harness,
                                model=model,
                                task=task,
                                repeat=repeat,
                            )
                        )
        return cells


class RunCell(BaseModel):
    """A single cell in the eval matrix: one harness × model × task × repeat."""

    run_name: str
    harness: HarnessSpec
    model: ModelSpec
    task: TaskSpec
    repeat: int
    budget: float | None = None
    """Per-cell budget estimate (USD) used for atomic reservation.

    If None, the orchestrator derives a reasonable estimate from the
    run-level ``budget_usd`` divided equally across all matrix cells.
    """

    @property
    def cell_id(self) -> str:
        return f"{self.harness.name}__{self.model.name}__{self.task.id}__r{self.repeat}"
