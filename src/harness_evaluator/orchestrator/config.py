"""Task and run configuration models."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Re-exported from the gateway models module to keep a single source of truth.
from harness_evaluator.gateway.models import ObservabilityTier

__all__ = [
    "ObservabilityTier",
    "AuthMode",
    "CostMode",
    "TaskTrack",
    "TaskDifficulty",
    "PhaseSpec",
    "PhaseInput",
    "TaskSpec",
    "TaskLibrary",
    "HarnessSpec",
    "ModelSpec",
    "ModelRole",
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


def resolve_task_repo_path(repo_url: str, lib_root: str | Path) -> Path:
    """Resolve a local task ``repo_url`` to an absolute path.

    Task YAMLs historically use ``repo_url: tasks/repos/<id>`` (relative to the
    repo root) while the fixtures live at ``<lib_root>/repos/<id>``, so a leading
    ``tasks/`` segment is stripped before resolving against ``lib_root``. This is
    the single source of truth for that mapping: resolving against the library
    root (rather than a ``__file__``-derived project root) is what makes a
    bundled ``harness_evaluator/tasks`` inside an installed wheel work.

    A relative path must stay under ``lib_root`` -- ``../..`` traversal raises
    ``ValueError``. An absolute path is honoured as-is: pointing a custom task
    library at a repo elsewhere on disk is legitimate, and task YAMLs are
    trusted input (their ``test_command`` already runs on the host).
    """
    base = Path(lib_root).resolve()
    candidate = Path(repo_url)
    if candidate.is_absolute():
        return candidate.resolve()
    if candidate.parts and candidate.parts[0] == "tasks":
        candidate = Path(*candidate.parts[1:])
    resolved = (base / candidate).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(
            f"Task repo_url '{repo_url}' escapes the task library root '{base}'."
        )
    return resolved


def default_docker_image() -> str:
    """Return the default runner image.

    Defaults to ``:latest`` rather than version-pinned because:
    - The installed package version may be ahead of the latest published
      Docker image (e.g. dev installs, release-please bumps).
    - Version-tagged images only exist for released versions on GHCR.
    - ``:latest`` is always available once the first image is published.

    To pin a specific image for reproducibility, set ``docker_image``
    explicitly in the run config (e.g.
    ``ghcr.io/yorch/harness-evaluator-runner:v0.5.0``).
    """
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
    MULTI_PHASE = "multi_phase"


class TaskDifficulty(StrEnum):
    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ModelRole(StrEnum):
    """Role a model plays in a multi-phase task."""

    IMPLEMENTATION = "implementation"
    REVIEW = "review"


class PhaseInput(StrEnum):
    """What input a phase receives from prior phases."""

    NONE = "none"
    """No input from prior phases (standalone phase)."""
    DIFF = "diff"
    """Git diff produced by the prior implementation phase."""
    OUTPUT = "output"
    """Stdout/output text from the prior phase."""
    REVIEW_FEEDBACK = "review_feedback"
    """Feedback text from a prior review phase."""


class PhaseSpec(BaseModel):
    """A single phase in a multi-phase task.

    Phases execute sequentially. Each phase runs a harness with a
    model assigned to the phase's ``model_role``. A phase can receive
    input from a prior phase (e.g. the diff from an implementation
    phase, or feedback from a review phase).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    """Phase identifier (e.g. 'implement', 'review', 'revise')."""
    model_role: ModelRole = ModelRole.IMPLEMENTATION
    """Which model role to use for this phase."""
    task_prompt: str
    """The prompt given to the harness for this phase."""
    input: PhaseInput = PhaseInput.NONE
    """What to feed from prior phases into this phase's prompt."""
    timeout_seconds: int = 600
    """Per-phase timeout in seconds."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _SAFE_ID_RE.match(v):
            raise ValueError(
                f"Phase name '{v}' contains invalid characters. "
                f"Only [A-Za-z0-9._-] are allowed."
            )
        return v


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
    """The prompt given to the harness. Used as the sole prompt for
    single-phase tracks (swe, open_ended). Ignored when ``phases`` is
    non-empty (multi_phase track uses per-phase prompts)."""
    test_command: str | None = None
    """Command to run tests (e.g. 'pytest tests/test_foo.py')."""
    test_patch: str | None = None
    """Patch file with hidden tests to apply before evaluation."""
    expected_files: list[str] = Field(default_factory=list)
    """Files that should be modified/created by the harness."""
    timeout_seconds: int = 600
    metadata: dict[str, Any] = Field(default_factory=dict)
    phases: list[PhaseSpec] = Field(default_factory=list)
    """Ordered phases for multi_phase tasks. Empty for swe/open_ended."""

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

    @model_validator(mode="after")
    def validate_phases(self) -> TaskSpec:
        if self.track == TaskTrack.MULTI_PHASE and not self.phases:
            raise ValueError(
                f"Multi-phase task '{self.id}' must define at least one phase"
            )
        if self.phases:
            # Phase names must be unique (used in trace IDs and file paths).
            names = [p.name for p in self.phases]
            if len(names) != len(set(names)):
                dupes = [n for n in names if names.count(n) > 1]
                raise ValueError(
                    f"Multi-phase task '{self.id}' has duplicate phase "
                    f"names: {sorted(set(dupes))}"
                )
            # At least one implementation phase is required.
            if not any(p.model_role == ModelRole.IMPLEMENTATION for p in self.phases):
                raise ValueError(
                    f"Multi-phase task '{self.id}' must have at least one "
                    f"phase with model_role: implementation"
                )
        return self


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
    """Model identifier (e.g. 'claude-sonnet-5')."""
    provider: str
    """Provider ('anthropic', 'openai', or 'google')."""
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
    role: ModelRole = ModelRole.IMPLEMENTATION
    """Role this model plays in multi-phase tasks. For single-phase tracks
    (swe, open_ended), all models are treated as implementation models."""

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
    run_as_user: str | None = None
    """UID:GID the container runs as, e.g. ``"1000:1000"``.

    Defaults to the invoking user so files the harness writes into the mounted
    workdir are owned by them. Override for rootless Docker or userns-remap
    setups where the host UID is not the effective container UID.
    """
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
        """Load run config from a YAML file.

        Raises ``ValueError`` if the file is empty or does not parse to a
        mapping -- ``cls(**data)`` would otherwise raise a ``TypeError`` about
        arguments after ``**``, which says nothing useful to someone who just
        mistyped a config.
        """
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            found = "an empty file" if data is None else f"a {type(data).__name__}"
            raise ValueError(
                f"Run config must be a YAML mapping of settings; {path} is {found}."
            )
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

        Remote URLs (http/https/git/ssh) are left untouched.
        """
        url = task.repo_url
        if not url:
            return
        if url.startswith(("http://", "https://", "git@", "ssh://")):
            return
        task.repo_url = str(resolve_task_repo_path(url, lib_root))

    def build_matrix(self) -> list[RunCell]:
        """Build the full eval matrix: harness × model × task × repeat.

        For multi-phase tasks, the matrix expands per role: each
        implementation-model × review-model pair becomes a cell. For
        single-phase tracks (swe, open_ended), the matrix is the
        traditional harness × model × task × repeat cross product.

        Multi-phase tasks that have no REVIEW phase do not expand review
        models — the review model is only paired when a phase actually
        uses it.
        """
        tasks = self.expand_tasks()
        cells: list[RunCell] = []

        # Partition models by role for multi-phase expansion.
        impl_models = [m for m in self.models if m.role == ModelRole.IMPLEMENTATION]
        review_models = [m for m in self.models if m.role == ModelRole.REVIEW]

        for harness in self.harnesses:
            for task in tasks:
                if task.track == TaskTrack.MULTI_PHASE:
                    # Determine if the task has any REVIEW phases.
                    has_review_phase = any(
                        p.model_role == ModelRole.REVIEW for p in task.phases
                    )

                    if has_review_phase:
                        # Validate that implementation models exist.
                        if not impl_models:
                            raise ValueError(
                                f"Multi-phase task '{task.id}' has a review "
                                f"phase but no implementation models are "
                                f"configured. Add at least one model with "
                                f"role: implementation."
                            )
                        if not review_models:
                            raise ValueError(
                                f"Multi-phase task '{task.id}' has a review "
                                f"phase but no review models are configured. "
                                f"Add at least one model with role: review."
                            )
                        # Expand implementation × review model pairs.
                        for impl_model in impl_models:
                            for review_model in review_models:
                                for repeat in range(self.repeats):
                                    cells.append(
                                        RunCell(
                                            run_name=self.name,
                                            harness=harness,
                                            model=impl_model,
                                            task=task,
                                            repeat=repeat,
                                            review_model=review_model,
                                        )
                                    )
                    else:
                        # No review phase — just implementation models.
                        for model in impl_models or self.models:
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
                else:
                    # Single-phase: traditional harness × model × task × repeat.
                    for model in self.models:
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
    """A single cell in the eval matrix: one harness × model × task × repeat.

    For multi-phase tasks, ``review_model`` holds the adversarial
    reviewer model (if any). For single-phase tracks it is None.
    """

    run_name: str
    harness: HarnessSpec
    model: ModelSpec
    task: TaskSpec
    repeat: int
    review_model: ModelSpec | None = None
    """Adversarial review model for multi-phase tasks. None for
    single-phase tracks or multi-phase tasks without a review phase."""
    budget: float | None = None
    """Per-cell budget estimate (USD) used for atomic reservation.

    If None, the orchestrator derives a reasonable estimate from the
    run-level ``budget_usd`` divided equally across all matrix cells.
    """

    @property
    def cell_id(self) -> str:
        base = f"{self.harness.name}__{self.model.name}__{self.task.id}__r{self.repeat}"
        if self.review_model is not None:
            return f"{base}__rev-{self.review_model.name}"
        return base
