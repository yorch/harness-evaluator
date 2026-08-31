"""Tests for multi-phase task configuration and matrix expansion."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelRole,
    ModelSpec,
    PhaseInput,
    PhaseSpec,
    RunConfig,
    TaskSpec,
    TaskTrack,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def impl_model() -> ModelSpec:
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        role=ModelRole.IMPLEMENTATION,
    )


@pytest.fixture
def review_model() -> ModelSpec:
    return ModelSpec(
        name="claude-opus-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        role=ModelRole.REVIEW,
    )


@pytest.fixture
def multiphase_task() -> TaskSpec:
    return TaskSpec(
        id="mp-test-001",
        name="Multi-phase test task",
        track=TaskTrack.MULTI_PHASE,
        task_prompt="Fix the bug",
        test_command="pytest tests/",
        phases=[
            PhaseSpec(
                name="implement",
                model_role=ModelRole.IMPLEMENTATION,
                task_prompt="Fix the bug",
                input=PhaseInput.NONE,
            ),
            PhaseSpec(
                name="review",
                model_role=ModelRole.REVIEW,
                task_prompt="Review the diff",
                input=PhaseInput.DIFF,
            ),
            PhaseSpec(
                name="revise",
                model_role=ModelRole.IMPLEMENTATION,
                task_prompt="Address feedback",
                input=PhaseInput.REVIEW_FEEDBACK,
            ),
        ],
    )


@pytest.fixture
def swe_task() -> TaskSpec:
    return TaskSpec(
        id="swe-test-001",
        name="SWE test task",
        track=TaskTrack.SWE,
        task_prompt="Fix the bug",
        test_command="pytest tests/",
    )


@pytest.fixture
def tmp_task_dir(tmp_path, multiphase_task, swe_task):
    """Create a temporary task library with a multi-phase and a SWE task."""
    mp_yaml = {
        "tasks": [
            {
                "id": multiphase_task.id,
                "name": multiphase_task.name,
                "track": "multi_phase",
                "task_prompt": multiphase_task.task_prompt,
                "test_command": multiphase_task.test_command,
                "phases": [
                    {
                        "name": p.name,
                        "model_role": p.model_role.value,
                        "task_prompt": p.task_prompt,
                        "input": p.input.value,
                    }
                    for p in multiphase_task.phases
                ],
            }
        ]
    }
    swe_yaml = {
        "tasks": [
            {
                "id": swe_task.id,
                "name": swe_task.name,
                "track": "swe",
                "task_prompt": swe_task.task_prompt,
                "test_command": swe_task.test_command,
            }
        ]
    }
    (tmp_path / "mp.yaml").write_text(yaml.dump(mp_yaml))
    (tmp_path / "swe.yaml").write_text(yaml.dump(swe_yaml))
    return tmp_path


# ---------------------------------------------------------------------------
# PhaseSpec tests
# ---------------------------------------------------------------------------


class TestPhaseSpec:
    def test_valid_phase(self) -> None:
        phase = PhaseSpec(
            name="implement",
            model_role=ModelRole.IMPLEMENTATION,
            task_prompt="Do the thing",
        )
        assert phase.name == "implement"
        assert phase.model_role == ModelRole.IMPLEMENTATION
        assert phase.input == PhaseInput.NONE

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="invalid characters"):
            PhaseSpec(name="../evil", task_prompt="Do the thing")

    def test_default_timeout(self) -> None:
        phase = PhaseSpec(name="review", task_prompt="Review it")
        assert phase.timeout_seconds == 600


# ---------------------------------------------------------------------------
# TaskSpec multi-phase validation
# ---------------------------------------------------------------------------


class TestTaskSpecMultiPhase:
    def test_multiphase_requires_phases(self) -> None:
        with pytest.raises(ValidationError, match="must define at least one phase"):
            TaskSpec(
                id="mp-bad",
                name="Bad multi-phase",
                track=TaskTrack.MULTI_PHASE,
                task_prompt="Fix the bug",
            )

    def test_multiphase_with_phases_ok(self, multiphase_task: TaskSpec) -> None:
        assert multiphase_task.track == TaskTrack.MULTI_PHASE
        assert len(multiphase_task.phases) == 3

    def test_swe_task_phases_empty_by_default(self, swe_task: TaskSpec) -> None:
        assert swe_task.phases == []


# ---------------------------------------------------------------------------
# RunCell cell_id with review model
# ---------------------------------------------------------------------------


class TestRunCellCellId:
    def test_cell_id_without_review_model(
        self, impl_model: ModelSpec, swe_task: TaskSpec
    ) -> None:
        from harness_evaluator.orchestrator.config import RunCell

        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(name="claude-code", adapter="claude_code"),
            model=impl_model,
            task=swe_task,
            repeat=0,
        )
        assert cell.cell_id == "claude-code__claude-sonnet-4-20250514__swe-test-001__r0"

    def test_cell_id_with_review_model(
        self,
        impl_model: ModelSpec,
        review_model: ModelSpec,
        multiphase_task: TaskSpec,
    ) -> None:
        from harness_evaluator.orchestrator.config import RunCell

        cell = RunCell(
            run_name="test",
            harness=HarnessSpec(name="claude-code", adapter="claude_code"),
            model=impl_model,
            task=multiphase_task,
            repeat=0,
            review_model=review_model,
        )
        assert "__rev-claude-opus-4-20250514" in cell.cell_id


# ---------------------------------------------------------------------------
# Matrix expansion
# ---------------------------------------------------------------------------


class TestMatrixExpansion:
    def test_multiphase_with_review_model(
        self,
        impl_model: ModelSpec,
        review_model: ModelSpec,
        tmp_task_dir,
    ) -> None:
        """Multi-phase task with impl + review models → impl × review cells."""
        config = RunConfig(
            name="mp-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=[impl_model, review_model],
            tasks=["mp-test-001"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        cells = config.build_matrix()
        assert len(cells) == 1
        assert cells[0].model.name == "claude-sonnet-4-20250514"
        assert cells[0].review_model is not None
        assert cells[0].review_model.name == "claude-opus-4-20250514"

    def test_multiphase_without_review_model(
        self,
        impl_model: ModelSpec,
        tmp_task_dir,
    ) -> None:
        """Multi-phase task with only impl models → one cell per impl model.

        Uses a task without a REVIEW phase so no review model is needed.
        """
        # Add a no-review-phase multi-phase task to the library.
        mp_no_review = {
            "tasks": [
                {
                    "id": "mp-no-review-001",
                    "name": "MP no review",
                    "track": "multi_phase",
                    "task_prompt": "Fix the bug",
                    "test_command": "pytest tests/",
                    "phases": [
                        {
                            "name": "implement",
                            "model_role": "implementation",
                            "task_prompt": "Fix the bug",
                            "input": "none",
                        },
                        {
                            "name": "revise",
                            "model_role": "implementation",
                            "task_prompt": "Polish it",
                            "input": "output",
                        },
                    ],
                }
            ]
        }
        (tmp_task_dir / "mp_no_review.yaml").write_text(yaml.dump(mp_no_review))
        config = RunConfig(
            name="mp-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=[impl_model],
            tasks=["mp-no-review-001"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        cells = config.build_matrix()
        assert len(cells) == 1
        assert cells[0].review_model is None

    def test_multiphase_repeats(
        self,
        impl_model: ModelSpec,
        review_model: ModelSpec,
        tmp_task_dir,
    ) -> None:
        """Multi-phase with repeats=3 → 3 cells (1 impl × 1 review × 3 repeats)."""
        config = RunConfig(
            name="mp-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=[impl_model, review_model],
            tasks=["mp-test-001"],
            task_library_path=str(tmp_task_dir),
            repeats=3,
        )
        cells = config.build_matrix()
        assert len(cells) == 3

    def test_mixed_matrix(
        self,
        impl_model: ModelSpec,
        review_model: ModelSpec,
        tmp_task_dir,
    ) -> None:
        """Mix of SWE and multi-phase tasks in one run."""
        config = RunConfig(
            name="mixed-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=[impl_model, review_model],
            tasks=["mp-test-001", "swe-test-001"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        cells = config.build_matrix()
        # Multi-phase: 1 cell (impl × review)
        # SWE: 2 cells (impl model + review model, each as single-phase)
        assert len(cells) == 3

    def test_two_impl_two_review(
        self,
        tmp_task_dir,
    ) -> None:
        """2 impl models × 2 review models → 4 multi-phase cells."""
        models = [
            ModelSpec(
                name="sonnet",
                provider="anthropic",
                api_key_env="KEY",
                role=ModelRole.IMPLEMENTATION,
            ),
            ModelSpec(
                name="haiku",
                provider="anthropic",
                api_key_env="KEY",
                role=ModelRole.IMPLEMENTATION,
            ),
            ModelSpec(
                name="opus",
                provider="anthropic",
                api_key_env="KEY",
                role=ModelRole.REVIEW,
            ),
            ModelSpec(
                name="gpt4o",
                provider="openai",
                api_key_env="KEY",
                role=ModelRole.REVIEW,
            ),
        ]
        config = RunConfig(
            name="mp-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=models,
            tasks=["mp-test-001"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        cells = config.build_matrix()
        assert len(cells) == 4
        # Each cell should have a distinct impl × review pair
        pairs = {(c.model.name, c.review_model.name) for c in cells}
        assert pairs == {
            ("sonnet", "opus"),
            ("sonnet", "gpt4o"),
            ("haiku", "opus"),
            ("haiku", "gpt4o"),
        }

    def test_review_phase_no_impl_models_raises(
        self,
        review_model: ModelSpec,
        tmp_task_dir,
    ) -> None:
        """All-review models with a review-phase task should raise."""
        config = RunConfig(
            name="mp-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=[review_model],
            tasks=["mp-test-001"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        with pytest.raises(ValueError, match="no implementation models"):
            config.build_matrix()

    def test_review_phase_no_review_models_raises(
        self,
        impl_model: ModelSpec,
        tmp_task_dir,
    ) -> None:
        """Review-phase task with no review models should raise."""
        config = RunConfig(
            name="mp-test",
            harnesses=[HarnessSpec(name="claude-code", adapter="claude_code")],
            models=[impl_model],
            tasks=["mp-test-001"],
            task_library_path=str(tmp_task_dir),
            repeats=1,
        )
        with pytest.raises(ValueError, match="no review models"):
            config.build_matrix()


class TestPhaseValidation:
    def test_duplicate_phase_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate phase names"):
            TaskSpec(
                id="mp-dup",
                name="Dup",
                track=TaskTrack.MULTI_PHASE,
                task_prompt="Fix bug",
                phases=[
                    PhaseSpec(name="implement", task_prompt="Fix"),
                    PhaseSpec(name="implement", task_prompt="Fix again"),
                ],
            )

    def test_no_implementation_phase_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one phase"):
            TaskSpec(
                id="mp-noimpl",
                name="NoImpl",
                track=TaskTrack.MULTI_PHASE,
                task_prompt="Fix bug",
                phases=[
                    PhaseSpec(
                        name="review",
                        model_role=ModelRole.REVIEW,
                        task_prompt="Review",
                    ),
                ],
            )
