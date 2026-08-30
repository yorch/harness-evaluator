"""Task and run configuration models."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Re-exported from the gateway models module to keep a single source of truth.
from harness_evaluator.gateway.models import ObservabilityTier

__all__ = [
    "ObservabilityTier",
    "AuthMode",
    "CostMode",
    "TaskTrack",
    "TaskDifficulty",
    "TaskSpec",
    "TaskLibrary",
    "HarnessSpec",
    "ModelSpec",
    "RunConfig",
    "RunCell",
]

# Strict allow-list for identifiers used in file paths, container names,
# and database keys. Prevents path traversal and shell injection from
# user-supplied YAML/CLI input.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Git ref / SHA charset. Must not start with '-' (would be read as an option
# by git checkout) and disallows shell metacharacters and whitespace.
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
# Docker image reference charset (registry/repo[:tag][@digest]). No shell
# metacharacters; a leading '-' is additionally rejected by the validator.
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")


def _strip_image_tag(ref: str) -> str:
    """Return the image reference without its ``:tag`` or ``@digest``.

    Keeps a registry port intact (a ``:`` before the last ``/`` is a port,
    not a tag).
    """
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        return ref[:last_colon]
    return ref


def default_task_library() -> str:
    """Return the path to the bundled task library.

    Tasks are shipped inside the wheel at ``harness_evaluator/tasks`` (see the hatchling
    force-include in pyproject.toml), so an installed harness-evaluator can run without a
    repository checkout. Falls back to the repo-root ``tasks/`` directory when
    running from a source tree without the bundled copy.
    """
    import importlib.resources as resources

    try:
        bundled = resources.files("harness_evaluator") / "tasks"
        if bundled.is_dir():
            return str(bundled)
    except (ModuleNotFoundError, AttributeError):
        pass
    # Source-tree fallback: <repo>/tasks (config.py -> harness-evaluator -> src -> repo).
    return str(Path(__file__).resolve().parents[3] / "tasks")


def default_docker_image() -> str:
    """Return the default runner image, pinned to the installed harness-evaluator version.

    A given harness-evaluator version pairs with the matching published runner image so
    runs are reproducible. Falls back to ``:latest`` if the version is
    unavailable.
    """
    try:
        from harness_evaluator import __version__

        return f"ghcr.io/yorch/harness-evaluator-runner:{__version__}"
    except Exception:
        return "ghcr.io/yorch/harness-evaluator-runner:latest"


class AuthMode(StrEnum):
    API_KEY = "api_key"
    CLAUDE_OAUTH = "claude_oauth"
    CODEX_CHATGPT = "codex_chatgpt"


class CostMode(StrEnum):
    PLATFORM = "platform"
    SUBSCRIPTION = "subscription"


class TaskTrack(StrEnum):
    SWE = "swe"
    OPEN_ENDED = "open_ended"


class TaskDifficulty(StrEnum):
    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


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

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        # ``id`` becomes part of the cell_id and, in turn, an on-disk
        # workdir path and container name. Restrict it to a safe charset
        # to prevent path traversal (e.g. ``../../etc``) and shell/Docker
        # injection.
        if not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Task id '{v}' contains invalid characters. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v

    @field_validator("repo_commit")
    @classmethod
    def validate_repo_commit(cls, v: str | None) -> str | None:
        # ``repo_commit`` is passed to ``git checkout``. Restrict it to a
        # git ref / SHA charset so it cannot be interpreted as an option
        # (e.g. ``-b``) or inject additional arguments.
        if v is None:
            return v
        if not _SAFE_REF_RE.match(v):
            raise ValueError(
                f"Task repo_commit '{v}' is not a valid git ref/SHA. "
                f"Only [A-Za-z0-9._/-] are allowed and it may not start with '-'."
            )
        return v


class TaskLibrary(BaseModel):
    """A collection of tasks loaded from YAML."""

    tasks: list[TaskSpec] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> TaskLibrary:
        """Load tasks from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # An empty file yields None; a malformed file may yield a non-dict.
        if not isinstance(data, dict):
            if data is None:
                return cls(tasks=[])
            raise ValueError(f"Task file {path} must contain a mapping with a 'tasks' key.")
        tasks = [TaskSpec(**t) for t in (data.get("tasks") or [])]
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
    docker_image: str | None = None
    """Optional per-harness runner image override. Takes precedence over
    ``version`` and the run-level ``docker_image``. Use this to pin a specific
    harness version (e.g. an image built with a harness build arg)."""
    version: str | None = None
    """Optional shorthand: use this value as the image tag on the run-level
    image's repository (e.g. run image ``ghcr.io/yorch/harness-evaluator-runner:0.1.0`` +
    ``version: cc-2.0.0`` -> ``ghcr.io/yorch/harness-evaluator-runner:cc-2.0.0``). Ignored
    when ``docker_image`` is set. You are responsible for building/pushing the
    tagged image (see the Dockerfile harness build args)."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Harness name '{v}' contains invalid characters. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str | None) -> str | None:
        # Used as a Docker image tag; restrict to the tag charset.
        if v is not None and not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Harness version '{v}' is not a valid image tag. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v

    @field_validator("docker_image")
    @classmethod
    def validate_docker_image(cls, v: str | None) -> str | None:
        # Passed as a docker argv element; reject a leading '-' so it can't be
        # interpreted as a docker flag, and restrict to image-ref characters.
        if v is None:
            return v
        if v.startswith("-") or not _IMAGE_REF_RE.match(v):
            raise ValueError(f"Invalid docker_image reference: {v!r}")
        return v

    def resolve_image(self, run_default: str) -> str:
        """Resolve the runner image for this harness.

        Precedence: explicit ``docker_image`` > ``version`` (as a tag on the
        run-level image's repository) > the run-level ``docker_image``.
        """
        if self.docker_image:
            return self.docker_image
        if self.version:
            return f"{_strip_image_tag(run_default)}:{self.version}"
        return run_default


class ModelSpec(BaseModel):
    """A model configuration for evaluation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    """Model identifier (e.g. 'claude-sonnet-4-20250514')."""
    provider: str
    """Provider ('anthropic' or 'openai')."""
    api_key_env: str
    """Environment variable name for the API key."""
    auth_mode: AuthMode = AuthMode.API_KEY
    """Authentication mode: api_key, claude_oauth, or codex_chatgpt."""
    credentials_path: str | None = None
    """Path to an OAuth credential file on the host (for subscription auth)."""
    cost_mode: CostMode = CostMode.PLATFORM
    """Cost accounting mode: 'platform' (pay-per-token) or 'subscription'
    (zero-dollar token-only accounting)."""
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
    task_library_path: str = Field(default_factory=default_task_library)
    """Path to the task library. Defaults to the bundled library so an
    installed harness-evaluator works without a repo checkout."""
    repeats: int = 5
    budget_usd: float | None = None
    """Maximum total spend in USD. None = no cap."""
    gateway_host: str = "host.docker.internal"
    """Gateway host as seen from inside Docker containers."""
    gateway_port: int = 8877
    gateway_db: str = "harness_evaluator_gateway.db"
    results_db: str = "harness_evaluator_results.db"
    workdir: str = "./harness_evaluator_workdir"
    docker_image: str = Field(default_factory=default_docker_image)
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
        """Expand task IDs to full TaskSpecs from the library.

        Local ``repo_url`` fixtures are resolved to absolute paths relative to
        the task library root so they work whether the library is the repo's
        ``tasks/`` dir or the bundled ``harness_evaluator/tasks`` inside an installed wheel.
        """
        lib = TaskLibrary.from_directory(self.task_library_path)
        lib_root = Path(self.task_library_path).resolve()
        for task in lib.tasks:
            self._normalize_repo_url(task, lib_root)
        if "*" in self.tasks:
            return lib.tasks
        task_map = {t.id: t for t in lib.tasks}
        missing = [tid for tid in self.tasks if tid not in task_map]
        if missing:
            raise ValueError(f"Unknown task IDs: {missing}. Available: {list(task_map.keys())}")
        return [task_map[tid] for tid in self.tasks]

    @staticmethod
    def _normalize_repo_url(task: TaskSpec, lib_root: Path) -> None:
        """Rewrite a local repo_url to an absolute path under the library.

        Task YAMLs historically use ``repo_url: tasks/repos/<id>`` (relative to
        the repo root). The fixtures live under ``<lib_root>/repos/<id>``, so a
        leading ``tasks/`` segment is stripped and the path is resolved against
        the library root. Remote URLs (http/https/git/ssh) are left untouched.
        """
        url = task.repo_url
        if not url:
            return
        if url.startswith(("http://", "https://", "git@", "ssh://")):
            return
        if Path(url).is_absolute():
            return
        rel = Path(url)
        if rel.parts and rel.parts[0] == "tasks":
            rel = Path(*rel.parts[1:])
        task.repo_url = str((lib_root / rel).resolve())

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
