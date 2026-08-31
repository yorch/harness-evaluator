"""Tests for the Gemini CLI adapter."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.gemini import GeminiAdapter
from harness_evaluator.adapters.registry import create_adapter, get_adapter_class
from harness_evaluator.orchestrator.config import ModelSpec


@pytest.fixture
def google_model() -> ModelSpec:
    return ModelSpec(
        name="gemini-2.5-pro",
        provider="google",
        api_key_env="GEMINI_API_KEY",
    )


@pytest.fixture
def tmp_workdir(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return workdir


class TestGeminiInfo:
    def test_info_returns_correct_metadata(self) -> None:
        info = GeminiAdapter.info()
        assert info.name == "gemini"
        assert info.display_name == "Gemini CLI (Google)"
        assert info.observability_tier == "partial"
        assert "npm install -g @google/gemini-cli" in info.install_instructions

    def test_adapter_registered(self) -> None:
        cls = get_adapter_class("gemini")
        assert cls is GeminiAdapter


class TestGeminiGetCommand:
    def test_command_uses_print_mode(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "gemini", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "gemini"
        assert "-p" in cmd
        assert "fix the bug" in cmd
        assert "--model" in cmd
        assert "gemini-2.5-pro" in cmd

    def test_command_includes_json_output_format(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "gemini", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test prompt")
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    def test_command_respects_text_output_format(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "gemini",
            workdir=str(tmp_workdir),
            model=google_model,
            config={"output_format": "text"},
        )
        assert adapter is not None
        cmd = adapter.get_command("test prompt")
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "text"

    def test_command_uses_bare_binary_name(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "gemini", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert cmd[0] == "gemini"
        assert "/" not in cmd[0]


class TestGeminiParseUsage:
    def test_parses_valid_json_usage(self) -> None:
        stdout = (
            '{"stats": {"models": {"gemini-2.5-pro": {"tokens": '
            '{"prompt": 1234, "candidates": 567, "cached": 100, '
            '"thoughts": 200, "total": 2101}}}}}'
        )
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 1234
        assert usage.output_tokens == 567
        assert usage.cache_read_tokens == 100
        assert usage.reasoning_tokens == 200

    def test_parses_ansi_escaped_output(self) -> None:
        stdout = (
            '\x1b[32m{"stats": {"models": {"gemini-2.5-pro": '
            '{"tokens": {"prompt": 100, "candidates": 50, '
            '"total": 150}}}}}\x1b[0m'
        )
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_returns_none_for_empty_output(self) -> None:
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        assert adapter.parse_self_reported_usage("", "") is None

    def test_returns_none_for_non_json(self) -> None:
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        assert adapter.parse_self_reported_usage("not json at all", "") is None

    def test_returns_none_for_no_stats(self) -> None:
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        stdout = '{"result": "success"}'
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_returns_none_for_zero_tokens(self) -> None:
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        stdout = (
            '{"stats": {"models": {"gemini-2.5-pro": '
            '{"tokens": {"prompt": 0, "candidates": 0, "total": 0}}}}}'
        )
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_parses_from_stderr(self) -> None:
        stderr = (
            '{"stats": {"models": {"gemini-2.5-pro": '
            '{"tokens": {"prompt": 500, "candidates": 200, '
            '"total": 700}}}}}'
        )
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        usage = adapter.parse_self_reported_usage("", stderr)
        assert usage is not None
        assert usage.input_tokens == 500

    def test_aggregates_multi_model_usage(self) -> None:
        stdout = (
            '{"stats": {"models": {'
            '"gemini-2.5-pro": {"tokens": {"prompt": 100, "candidates": 50}}, '
            '"gemini-2.5-flash": {"tokens": {"prompt": 200, "candidates": 100}}'
            '}}}'
        )
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 300
        assert usage.output_tokens == 150


class TestGeminiGetEnv:
    def test_sets_gemini_api_key_for_google_provider(
        self, tmp_workdir, google_model, monkeypatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
        adapter = create_adapter(
            "gemini", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env.get("GEMINI_API_KEY") == "test-key-123"
        assert env.get("GOOGLE_API_KEY") == "test-key-123"

    def test_does_not_set_gemini_api_key_for_non_google(
        self, tmp_workdir, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        model = ModelSpec(
            name="gpt-4o", provider="openai", api_key_env="OPENAI_API_KEY"
        )
        adapter = create_adapter(
            "gemini", workdir=str(tmp_workdir), model=model
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "GEMINI_API_KEY" not in env
        assert "GOOGLE_API_KEY" not in env

    def test_does_not_set_base_url(
        self, tmp_workdir, google_model, monkeypatch
    ) -> None:
        """GOOGLE_GEMINI_BASE_URL is not set because the gateway doesn't
        support Google API routing yet."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        adapter = create_adapter(
            "gemini",
            workdir=str(tmp_workdir),
            model=google_model,
            gateway_url="http://localhost:8877",
            trace_id="cell-123",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "GOOGLE_GEMINI_BASE_URL" not in env
