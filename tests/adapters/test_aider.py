"""Tests for the Aider adapter."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.aider import AiderAdapter
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


class TestAiderInfo:
    def test_info_returns_correct_metadata(self) -> None:
        info = AiderAdapter.info()
        assert info.name == "aider"
        assert info.display_name == "Aider"
        assert info.observability_tier == "full"
        assert "pip install aider-chat" in info.install_instructions

    def test_adapter_registered(self) -> None:
        cls = get_adapter_class("aider")
        assert cls is AiderAdapter


class TestAiderGetCommand:
    def test_command_includes_message_and_model(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "aider", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "aider"
        assert "--message" in cmd
        assert "fix the bug" in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-20250514" in cmd

    def test_command_includes_yes_and_no_auto_commits(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "aider", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test prompt")
        assert "--yes" in cmd
        assert "--no-auto-commits" in cmd

    def test_command_uses_bare_binary_name(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "aider", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert cmd[0] == "aider"
        assert "/" not in cmd[0]

    def test_command_includes_extra_args(
        self, tmp_workdir, anthropic_model
    ) -> None:
        adapter = create_adapter(
            "aider",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            config={"extra_args": ["--no-stream", "--verbose"]},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--no-stream" in cmd
        assert "--verbose" in cmd


class TestAiderParseUsage:
    def test_parses_simple_tokens_line(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "Tokens: 1234 sent, 567 received"
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 1234
        assert usage.output_tokens == 567

    def test_parses_comma_separated_numbers(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "Tokens: 1,234,567 sent, 89,012 received"
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 1234567
        assert usage.output_tokens == 89012

    def test_parses_with_surrounding_text(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "Some output\nTokens: 100 sent, 50 received\nMore output"
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_returns_last_usage_line(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "Tokens: 100 sent, 50 received\nTokens: 200 sent, 100 received"
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 200
        assert usage.output_tokens == 100

    def test_parses_ansi_escaped_output(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = '\x1b[32mTokens: 100 sent, 50 received\x1b[0m'
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100

    def test_parses_case_insensitive(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "tokens: 100 sent, 50 received"
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100

    def test_parses_from_stderr(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stderr = "Tokens: 300 sent, 150 received"
        usage = adapter.parse_self_reported_usage("", stderr)
        assert usage is not None
        assert usage.input_tokens == 300

    def test_returns_none_for_empty_output(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        assert adapter.parse_self_reported_usage("", "") is None

    def test_returns_none_for_no_tokens_line(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "Some output without token info"
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_returns_none_for_zero_tokens(self) -> None:
        adapter = AiderAdapter.__new__(AiderAdapter)
        stdout = "Tokens: 0 sent, 0 received"
        assert adapter.parse_self_reported_usage(stdout, "") is None


class TestAiderGetEnv:
    def test_forwards_deepseek_api_key(
        self, tmp_workdir, monkeypatch
    ) -> None:
        """Aider supports DeepSeek; the key should be forwarded."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key-123")
        model = ModelSpec(
            name="deepseek-coder",
            provider="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
        )
        adapter = create_adapter(
            "aider", workdir=str(tmp_workdir), model=model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env.get("DEEPSEEK_API_KEY") == "ds-key-123"

    def test_does_not_double_set_anthropic_key(
        self, tmp_workdir, anthropic_model, monkeypatch
    ) -> None:
        """Anthropic key is handled by the base class; Aider should not
        add it again (but it's fine if it's present)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        adapter = create_adapter(
            "aider", workdir=str(tmp_workdir), model=anthropic_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env.get("ANTHROPIC_API_KEY") == "sk-ant-test"
