"""Tests for OpenCode and Pi adapter command construction.

Covers gaps identified by adversarial review:
- OpenCode model_flag override + non-anthropic provider/name format
- Pi model_flag override + default model name
- Gateway URL with trace_id for opencode and pi (base behavior)
"""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.opencode import OpenCodeAdapter
from harness_evaluator.adapters.pi import PiAdapter
from harness_evaluator.orchestrator.config import ModelSpec


@pytest.fixture
def anthropic_model() -> ModelSpec:
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )


@pytest.fixture
def openai_model() -> ModelSpec:
    return ModelSpec(
        name="gpt-4o",
        provider="openai",
        api_key_env="OPENAI_API_KEY",
    )


@pytest.fixture
def tmp_workdir(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return workdir


class TestOpenCodeGetCommand:
    """Tests for OpenCodeAdapter.get_command()."""

    def test_default_model_uses_provider_name_format(
        self, tmp_workdir, anthropic_model
    ) -> None:
        """The default --model value is provider/name format."""
        adapter = OpenCodeAdapter(
            workdir=str(tmp_workdir), model=anthropic_model
        )
        cmd = adapter.get_command("refactor code")
        i = cmd.index("--model")
        assert cmd[i + 1] == "anthropic/claude-sonnet-4-20250514"

    def test_default_model_openai_provider(
        self, tmp_workdir, openai_model
    ) -> None:
        """Non-anthropic providers produce provider/name, not hardcoded anthropic."""
        adapter = OpenCodeAdapter(
            workdir=str(tmp_workdir), model=openai_model
        )
        cmd = adapter.get_command("refactor code")
        i = cmd.index("--model")
        assert cmd[i + 1] == "openai/gpt-4o"

    def test_model_flag_override(
        self, tmp_workdir, anthropic_model
    ) -> None:
        """An explicit model_flag in config overrides the provider/name format."""
        adapter = OpenCodeAdapter(
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"model_flag": "custom-provider/custom-model"},
        )
        cmd = adapter.get_command("refactor code")
        i = cmd.index("--model")
        assert cmd[i + 1] == "custom-provider/custom-model"

    def test_command_shape(self, tmp_workdir, anthropic_model) -> None:
        """The command uses `opencode run <prompt> --model ...`."""
        adapter = OpenCodeAdapter(
            workdir=str(tmp_workdir), model=anthropic_model
        )
        cmd = adapter.get_command("do work")
        assert cmd[0] == "opencode"
        assert cmd[1] == "run"
        assert "do work" in cmd
        assert "--model" in cmd


class TestOpenCodeGatewayEnv:
    """OpenCode inherits gateway env behavior from BaseAdapter; verify it."""

    def test_anthropic_gateway_env_with_trace(
        self, tmp_workdir, anthropic_model
    ) -> None:
        """OpenCode + anthropic provider + trace_id gets the /v1 suffix.

        This previously asserted a base URL without ``/v1``, matching what the
        code did rather than what OpenCode needs: its AI SDK client then
        requested ``/messages``, the gateway answered 404, and the cell made no
        API call at all. The openai case below has always expected ``/v1``.
        """
        adapter = OpenCodeAdapter(
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="oc-cell-1",
        )
        env = adapter.get_env()
        assert env["ANTHROPIC_BASE_URL"] == (
            "http://127.0.0.1:8877/__trace__/oc-cell-1/v1"
        )
        assert env["HARNESS_EVALUATOR_TRACE_ID"] == "oc-cell-1"

    def test_openai_gateway_env_with_trace(
        self, tmp_workdir, openai_model
    ) -> None:
        """OpenCode + openai provider + trace_id gets the /v1 suffix."""
        adapter = OpenCodeAdapter(
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="oc-cell-2",
        )
        env = adapter.get_env()
        assert env["OPENAI_BASE_URL"] == (
            "http://127.0.0.1:8877/__trace__/oc-cell-2/v1"
        )


class TestPiGetCommand:
    """Tests for PiAdapter.get_command()."""

    def test_default_model_uses_model_name(
        self, tmp_workdir
    ) -> None:
        """By default --model is set to the ModelSpec name, not hardcoded."""
        model = ModelSpec(
            name="test-pi-model-xyz",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        )
        adapter = PiAdapter(
            workdir=str(tmp_workdir), model=model
        )
        cmd = adapter.get_command("write tests")
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "test-pi-model-xyz"

    def test_model_flag_override(
        self, tmp_workdir, anthropic_model
    ) -> None:
        """A model_flag in config overrides the default model name."""
        adapter = PiAdapter(
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"model_flag": "override-model"},
        )
        cmd = adapter.get_command("write tests")
        assert cmd[cmd.index("--model") + 1] == "override-model"

    def test_command_shape(self, tmp_workdir, anthropic_model) -> None:
        """The command uses `pi -p <prompt> --model ...`."""
        adapter = PiAdapter(
            workdir=str(tmp_workdir), model=anthropic_model
        )
        cmd = adapter.get_command("write tests")
        assert cmd[0] == "pi"
        assert "-p" in cmd
        assert "write tests" in cmd


class TestPiGatewayEnv:
    """Pi inherits gateway env behavior from BaseAdapter; verify it."""

    def test_anthropic_gateway_env_with_trace(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = PiAdapter(
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="pi-cell-1",
        )
        env = adapter.get_env()
        assert env["ANTHROPIC_BASE_URL"] == (
            "http://127.0.0.1:8877/__trace__/pi-cell-1"
        )
        assert env["HARNESS_EVALUATOR_TRACE_ID"] == "pi-cell-1"
