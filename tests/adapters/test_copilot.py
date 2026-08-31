"""Tests for the GitHub Copilot CLI adapter."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.copilot import CopilotAdapter
from harness_evaluator.adapters.registry import create_adapter, get_adapter_class
from harness_evaluator.orchestrator.config import ModelSpec


@pytest.fixture
def anthropic_model() -> ModelSpec:
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )


@pytest.fixture
def tmp_workdir(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return workdir


class TestCopilotInfo:
    def test_info_returns_correct_metadata(self) -> None:
        info = CopilotAdapter.info()
        assert info.name == "copilot"
        assert info.display_name == "GitHub Copilot CLI"
        assert info.observability_tier == "minimal"
        assert "npm install -g @github/copilot" in info.install_instructions

    def test_adapter_registered(self) -> None:
        cls = get_adapter_class("copilot")
        assert cls is CopilotAdapter


class TestCopilotGetCommand:
    def test_command_uses_print_mode(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "copilot"
        assert "-p" in cmd
        assert "fix the bug" in cmd

    def test_command_includes_model(self, tmp_workdir, anthropic_model) -> None:
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--model" in cmd
        assert "claude-sonnet-4-20250514" in cmd

    def test_command_includes_silent_and_no_ask(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "-s" in cmd
        assert "--no-ask-user" in cmd

    def test_command_includes_allow_all_tools_by_default(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--allow-all-tools" in cmd

    def test_command_can_disable_allow_all_tools(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "copilot",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"disable_allow_all_tools": True},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--allow-all-tools" not in cmd

    def test_command_uses_bare_binary_name(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert cmd[0] == "copilot"
        assert "/" not in cmd[0]


class TestCopilotGetEnv:
    def test_does_not_set_api_keys(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_does_not_set_base_urls(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "copilot",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://localhost:8877",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "ANTHROPIC_BASE_URL" not in env
        assert "OPENAI_BASE_URL" not in env

    def test_forwards_github_token(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp-test-token")
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env.get("GITHUB_TOKEN") == "ghp-test-token"

    def test_passes_through_allowlist(self, tmp_workdir, anthropic_model) -> None:
        adapter = create_adapter(
            "copilot", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "PATH" in env
