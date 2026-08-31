"""Tests for the BaseAdapter env construction, focused on OpenAI /v1 routing."""

from __future__ import annotations

import pytest

from harness_evaluator.adapters.registry import create_adapter, get_adapter_class
from harness_evaluator.orchestrator.config import ModelSpec


@pytest.fixture
def openai_model() -> ModelSpec:
    return ModelSpec(
        name="gpt-4o",
        provider="openai",
        api_key_env="OPENAI_API_KEY",
    )


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


def _adapter_cls(name: str):
    """Resolve an adapter class via the registry (triggers lazy load)."""
    cls = get_adapter_class(name)
    assert cls is not None, f"{name} adapter not registered"
    return cls


class TestOpenAIBaseUrlV1Suffix:
    """Verify OPENAI_BASE_URL ends with /v1 (or /__trace__/.../v1)."""

    def test_openai_base_url_ends_with_v1(
        self, tmp_workdir, openai_model
    ) -> None:
        """OPENAI_BASE_URL for an OpenAI adapter must end with /v1."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877",
        )
        assert adapter is not None
        env = adapter.get_env()
        url = env["OPENAI_BASE_URL"]
        assert url.endswith("/v1"), (
            f"OPENAI_BASE_URL should end with /v1, got: {url}"
        )

    def test_openai_base_url_ends_with_v1_and_trace(
        self, tmp_workdir, openai_model
    ) -> None:
        """With a trace_id, OPENAI_BASE_URL has /__trace__/<id>/v1 path."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="cell-xyz",
        )
        assert adapter is not None
        env = adapter.get_env()
        url = env["OPENAI_BASE_URL"]
        assert "/__trace__/cell-xyz/v1" in url, (
            f"OPENAI_BASE_URL should contain /__trace__/cell-xyz/v1, got: {url}"
        )
        assert url.endswith("/v1"), (
            f"OPENAI_BASE_URL should end with /v1, got: {url}"
        )

    def test_openai_base_url_v1_not_duplicated(
        self, tmp_workdir, openai_model
    ) -> None:
        """If the gateway URL already ends with /v1, it is not duplicated."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877/v1",
            trace_id="cell-dup",
        )
        assert adapter is not None
        env = adapter.get_env()
        url = env["OPENAI_BASE_URL"]
        # Should be /__trace__/cell-dup/v1, not /v1/v1 or /__trace__/cell-dup/v1/v1
        assert url == "http://127.0.0.1:8877/__trace__/cell-dup/v1", (
            f"OPENAI_BASE_URL should not duplicate /v1, got: {url}"
        )

    def test_anthropic_base_url_no_v1_suffix(
        self, tmp_workdir, anthropic_model
    ) -> None:
        """Anthropic provider must NOT get a /v1 suffix appended."""
        # Instantiate directly to avoid registry lazy-load ordering issues.
        cls = _adapter_cls("claude-code")
        adapter = cls(
            workdir=str(tmp_workdir),
            model=anthropic_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id="anthropic-cell",
        )
        env = adapter.get_env()
        url = env["ANTHROPIC_BASE_URL"]
        assert "/v1" not in url, (
            f"ANTHROPIC_BASE_URL should not have /v1, got: {url}"
        )
        assert url == "http://127.0.0.1:8877/__trace__/anthropic-cell"
