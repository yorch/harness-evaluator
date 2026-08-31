"""Tests for the Cursor CLI adapter."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.cursor import CursorAdapter
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


class TestCursorInfo:
    def test_info_returns_correct_metadata(self) -> None:
        info = CursorAdapter.info()
        assert info.name == "cursor"
        assert info.display_name == "Cursor CLI"
        assert info.observability_tier == "minimal"

    def test_adapter_registered(self) -> None:
        cls = get_adapter_class("cursor")
        assert cls is CursorAdapter


class TestCursorGetCommand:
    def test_command_uses_agent_binary(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "agent"
        assert "-p" in cmd
        assert "fix the bug" in cmd

    def test_command_includes_model(self, tmp_workdir, anthropic_model) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--model" in cmd
        assert "claude-sonnet-4-20250514" in cmd

    def test_command_includes_force_by_default(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--force" in cmd

    def test_command_includes_trust(self, tmp_workdir, anthropic_model) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--trust" in cmd

    def test_command_can_disable_force(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"force": False},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--force" not in cmd

    def test_command_uses_bare_binary_name(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert cmd[0] == "agent"
        assert "/" not in cmd[0]

    def test_command_includes_mode_when_non_default(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"mode": "plan"},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "plan"

    def test_command_omits_mode_when_default(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--mode" not in cmd


class TestCursorGetEnv:
    def test_does_not_set_api_keys(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_does_not_set_base_urls(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "cursor",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://localhost:8877",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "ANTHROPIC_BASE_URL" not in env
        assert "OPENAI_BASE_URL" not in env

    def test_forwards_cursor_api_key(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("CURSOR_API_KEY", "cursor-key-123")
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env.get("CURSOR_API_KEY") == "cursor-key-123"

    def test_passes_through_allowlist(self, tmp_workdir, anthropic_model) -> None:
        adapter = create_adapter(
            "cursor", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "PATH" in env
