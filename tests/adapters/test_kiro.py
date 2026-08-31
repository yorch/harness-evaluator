"""Tests for the Kiro CLI adapter."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.kiro import KiroAdapter
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


class TestKiroInfo:
    def test_info_returns_correct_metadata(self) -> None:
        info = KiroAdapter.info()
        assert info.name == "kiro"
        assert info.display_name == "Kiro CLI (AWS)"
        assert info.observability_tier == "minimal"

    def test_adapter_registered(self) -> None:
        cls = get_adapter_class("kiro")
        assert cls is KiroAdapter


class TestKiroGetCommand:
    def test_command_uses_chat_no_interactive(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "kiro-cli"
        assert "chat" in cmd
        assert "--no-interactive" in cmd
        assert "fix the bug" in cmd

    def test_command_includes_model(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--model" in cmd
        assert "claude-sonnet-4-20250514" in cmd

    def test_command_includes_trust_all_tools_by_default(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--trust-all-tools" in cmd

    def test_command_uses_trust_tools_when_configured(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"trust_tools": ["tool1", "tool2"]},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        # Should use --trust-tools=tool1,tool2 (single = token)
        trust_arg = next(
            (c for c in cmd if c.startswith("--trust-tools")), None
        )
        assert trust_arg is not None
        assert trust_arg == "--trust-tools=tool1,tool2"
        assert "--trust-all-tools" not in cmd

    def test_command_includes_effort_when_configured(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"effort": "high"},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--effort" in cmd
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"

    def test_command_includes_agent_when_configured(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"agent": "code-reviewer"},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "code-reviewer"

    def test_command_uses_bare_binary_name(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert cmd[0] == "kiro-cli"
        assert "/" not in cmd[0]


class TestKiroGetEnv:
    def test_does_not_set_api_keys(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_does_not_set_base_urls(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "kiro",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://localhost:8877",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "ANTHROPIC_BASE_URL" not in env
        assert "OPENAI_BASE_URL" not in env

    def test_forwards_kiro_api_key(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("KIRO_API_KEY", "kiro-key-123")
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env.get("KIRO_API_KEY") == "kiro-key-123"

    def test_passes_through_allowlist(self, tmp_workdir, anthropic_model) -> None:
        adapter = create_adapter(
            "kiro", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "PATH" in env
