"""Tests for the Antigravity CLI adapter."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.antigravity import AntigravityAdapter
from harness_evaluator.adapters.registry import create_adapter, get_adapter_class
from harness_evaluator.orchestrator.config import ModelSpec


@pytest.fixture
def google_model() -> ModelSpec:
    return ModelSpec(
        name="gemini-3-pro",
        provider="google",
        api_key_env="GOOGLE_API_KEY",
    )


@pytest.fixture
def tmp_workdir(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return workdir


class TestAntigravityInfo:
    def test_info_returns_correct_metadata(self) -> None:
        info = AntigravityAdapter.info()
        assert info.name == "antigravity"
        assert info.display_name == "Antigravity CLI (Google)"
        assert info.observability_tier == "partial"

    def test_adapter_registered(self) -> None:
        cls = get_adapter_class("antigravity")
        assert cls is AntigravityAdapter


class TestAntigravityGetCommand:
    def test_command_uses_print_mode(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "antigravity", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "agy"
        assert "-p" in cmd
        assert "fix the bug" in cmd
        assert "--model" in cmd
        assert "gemini-3-pro" in cmd

    def test_command_includes_json_output_format(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "antigravity", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    def test_command_respects_text_output_format(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "antigravity",
            workdir=str(tmp_workdir),
            model=google_model,
            config={"output_format": "text"},
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "text"

    def test_command_uses_bare_binary_name(
        self, tmp_workdir, google_model
    ) -> None:
        adapter = create_adapter(
            "antigravity", workdir=str(tmp_workdir), model=google_model
        )
        assert adapter is not None
        cmd = adapter.get_command("test")
        assert cmd[0] == "agy"
        assert "/" not in cmd[0]


class TestAntigravityParseUsage:
    def test_parses_usage_field(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '{"usage": {"input_tokens": 1234, "output_tokens": 567}}'
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 1234
        assert usage.output_tokens == 567

    def test_parses_metadata_usage_nesting(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '{"metadata": {"usage": {"input_tokens": 500, "output_tokens": 200}}}'
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 500
        assert usage.output_tokens == 200

    def test_parses_top_level_tokens(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '{"input_tokens": 100, "output_tokens": 50}'
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100

    def test_parses_ansi_escaped_output(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '\x1b[32m{"usage": {"input_tokens": 100, "output_tokens": 50}}\x1b[0m'
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.input_tokens == 100

    def test_parses_from_stderr(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stderr = '{"usage": {"input_tokens": 300, "output_tokens": 150}}'
        usage = adapter.parse_self_reported_usage("", stderr)
        assert usage is not None
        assert usage.input_tokens == 300

    def test_returns_none_for_empty(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        assert adapter.parse_self_reported_usage("", "") is None

    def test_returns_none_for_non_json(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        assert adapter.parse_self_reported_usage("not json", "") is None

    def test_returns_none_for_no_usage(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '{"result": "success"}'
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_returns_none_for_zero_tokens(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '{"usage": {"input_tokens": 0, "output_tokens": 0}}'
        assert adapter.parse_self_reported_usage(stdout, "") is None

    def test_parses_cached_tokens(self) -> None:
        adapter = AntigravityAdapter.__new__(AntigravityAdapter)
        stdout = '{"usage": {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 30}}'
        usage = adapter.parse_self_reported_usage(stdout, "")
        assert usage is not None
        assert usage.cache_read_tokens == 30
