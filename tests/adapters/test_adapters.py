"""Tests for harness adapters."""

from __future__ import annotations

import os

import pytest

from heval.adapters.registry import create_adapter, get_adapter_class, list_adapters
from heval.orchestrator.config import ModelSpec


@pytest.fixture
def anthropic_model():
    return ModelSpec(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )


@pytest.fixture
def openai_model():
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


class TestAdapterRegistry:
    def test_list_adapters(self):
        adapters = list_adapters()
        assert "opencode" in adapters
        assert "claude-code" in adapters
        assert "codex" in adapters
        assert "pi" in adapters
        assert "omp" in adapters

    def test_get_adapter_class(self):
        cls = get_adapter_class("claude-code")
        assert cls is not None
        assert cls.__name__ == "ClaudeCodeAdapter"

    def test_get_unknown_adapter(self):
        cls = get_adapter_class("nonexistent")
        assert cls is None

    def test_create_adapter(self, tmp_workdir, anthropic_model):
        adapter = create_adapter(
            "claude-code",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        assert adapter.model.name == "claude-sonnet-4-20250514"

    def test_create_unknown_adapter(self, tmp_workdir, anthropic_model):
        adapter = create_adapter(
            "nonexistent",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is None


class TestAdapterInfo:
    def test_opencode_info(self):
        from heval.adapters.opencode import OpenCodeAdapter

        info = OpenCodeAdapter.info()
        assert info.name == "opencode"
        assert info.observability_tier == "full"

    def test_claude_code_info(self):
        from heval.adapters.claude_code import ClaudeCodeAdapter

        info = ClaudeCodeAdapter.info()
        assert info.name == "claude-code"
        assert info.observability_tier == "partial"

    def test_codex_info(self):
        from heval.adapters.codex import CodexAdapter

        info = CodexAdapter.info()
        assert info.name == "codex"
        assert info.observability_tier == "partial"

    def test_pi_info(self):
        from heval.adapters.pi import PiAdapter

        info = PiAdapter.info()
        assert info.name == "pi"
        assert info.observability_tier == "minimal"

    def test_omp_info(self):
        from heval.adapters.omp import OMPAdapter

        info = OMPAdapter.info()
        assert info.name == "omp"
        assert info.observability_tier == "minimal"


class TestAdapterEnv:
    def test_anthropic_gateway_env(self, tmp_workdir, anthropic_model):
        adapter = create_adapter(
            "claude-code",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="test-cell-1",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8877"
        assert env["HEVAL_TRACE_ID"] == "test-cell-1"

    def test_openai_gateway_env(self, tmp_workdir, openai_model):
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="test-cell-2",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8877"
        assert env["HEVAL_TRACE_ID"] == "test-cell-2"

    def test_no_gateway_url(self, tmp_workdir, anthropic_model):
        adapter = create_adapter(
            "claude-code",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        env = adapter.get_env()
        # Should not set BASE_URL if no gateway
        assert "ANTHROPIC_BASE_URL" not in env or env.get(
            "ANTHROPIC_BASE_URL"
        ) == os.environ.get("ANTHROPIC_BASE_URL", "")


class TestAdapterRunMissingExecutable:
    async def test_claude_not_installed(self, tmp_workdir, anthropic_model, monkeypatch):
        """Test that missing executable returns error result."""
        # Force shutil.which to return None

        monkeypatch.setattr(
            "heval.adapters.claude_code.shutil.which", lambda x: None
        )
        adapter = create_adapter(
            "claude-code",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        result = await adapter.run("test prompt", timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr

    async def test_codex_not_installed(self, tmp_workdir, openai_model, monkeypatch):
        """Test that missing codex returns error result."""
        monkeypatch.setattr("heval.adapters.codex.shutil.which", lambda x: None)
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
        )
        assert adapter is not None
        result = await adapter.run("test prompt", timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr

    async def test_opencode_not_installed(self, tmp_workdir, anthropic_model, monkeypatch):
        """Test that missing opencode returns error result."""
        monkeypatch.setattr("heval.adapters.opencode.shutil.which", lambda x: None)
        adapter = create_adapter(
            "opencode",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        result = await adapter.run("test prompt", timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr

    async def test_pi_not_installed(self, tmp_workdir, anthropic_model, monkeypatch):
        """Test that missing pi returns error result."""
        monkeypatch.setattr("heval.adapters.pi.shutil.which", lambda x: None)
        adapter = create_adapter(
            "pi",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        result = await adapter.run("test prompt", timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr

    async def test_omp_not_installed(self, tmp_workdir, anthropic_model, monkeypatch):
        """Test that missing omp returns error result."""
        monkeypatch.setattr("heval.adapters.omp.shutil.which", lambda x: None)
        adapter = create_adapter(
            "omp",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        result = await adapter.run("test prompt", timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr


class TestAdapterEnvAllowlist:
    def test_no_host_secret_leak(self, tmp_workdir, anthropic_model, monkeypatch):
        """Test that arbitrary host env vars are NOT passed through."""
        monkeypatch.setenv("SECRET_TOKEN", "super-secret-value")
        adapter = create_adapter(
            "claude-code",
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://127.0.0.1:8877",
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "SECRET_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" in env

    def test_path_is_passed_through(self, tmp_workdir, anthropic_model):
        """Test that PATH is in the allowlist."""
        adapter = create_adapter(
            "claude-code",
            workdir=str(tmp_workdir),
            model=anthropic_model,
        )
        assert adapter is not None
        env = adapter.get_env()
        assert "PATH" in env


class TestClaudeCodeAnsiStripping:
    def test_strip_ansi(self):
        from heval.adapters.claude_code import _strip_ansi

        text = "\x1B[32mhello\x1B[0m world"
        assert _strip_ansi(text) == "hello world"

    def test_strip_ansi_complex(self):
        from heval.adapters.claude_code import _strip_ansi

        text = "\x1B[1;31mError:\x1B[0m \x1B[4mnot found\x1B[24m"
        assert _strip_ansi(text) == "Error: not found"
