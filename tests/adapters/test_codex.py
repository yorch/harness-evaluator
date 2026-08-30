"""Tests for the Codex adapter command construction and trace propagation."""

from __future__ import annotations

import pytest

from harnessbench.adapters.registry import create_adapter, get_adapter_class
from harnessbench.orchestrator.config import ModelSpec


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


def _codex_cls():
    """Resolve the CodexAdapter class via the registry (triggers lazy load)."""
    cls = get_adapter_class("codex")
    assert cls is not None, "codex adapter not registered"
    return cls


class TestCodexGetCommand:
    """Tests for CodexAdapter.get_command()."""

    def test_command_includes_exec_and_model(
        self, tmp_workdir, openai_model
    ) -> None:
        """The command uses `codex exec` with the configured model."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "gpt-4o" in cmd
        assert cmd[-1] == "fix the bug"

    def test_command_includes_trace_aware_gateway_url(
        self, tmp_workdir, openai_model, monkeypatch
    ) -> None:
        """get_command() embeds the trace-aware gateway URL with trace_id=.

        When HARNESSBENCH_TRACE_ID is set in the env (mirrored from the adapter's
        trace_id), the gateway URL passed to codex via ``-c
        openai_base_url=...`` must include the ``trace_id=`` query param so
        the gateway can attribute provider calls to this cell.
        """
        trace_id = "codex-cell-abc-123"
        monkeypatch.setenv("HARNESSBENCH_TRACE_ID", trace_id)

        cls = _codex_cls()
        adapter = cls(
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877",
            trace_id=trace_id,
        )
        cmd = adapter.get_command("fix the bug")

        # Find the -c openai_base_url=... config override in the command.
        base_url_args = [
            arg for arg in cmd if arg.startswith("openai_base_url=")
        ]
        assert base_url_args, (
            "get_command() did not include openai_base_url config override"
        )
        base_url_arg = base_url_args[0]

        # The URL must carry the trace_id query param.
        assert "trace_id=codex-cell-abc-123" in base_url_arg, (
            f"trace_id query param missing from gateway URL: {base_url_arg}"
        )
        # The OpenAI base URL must end with /v1 (with trace appended).
        assert "/v1" in base_url_arg, (
            f"gateway URL missing /v1 suffix: {base_url_arg}"
        )

    def test_command_no_gateway_url_when_unset(
        self, tmp_workdir, openai_model
    ) -> None:
        """No openai_base_url override when gateway_url is not configured."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        assert not [
            arg for arg in cmd if arg.startswith("openai_base_url=")
        ]

    def test_command_no_trace_id_when_unset(
        self, tmp_workdir, openai_model
    ) -> None:
        """Without a trace_id, the gateway URL has no trace_id query param."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://127.0.0.1:8877",
        )
        assert adapter is not None
        cmd = adapter.get_command("fix the bug")
        base_url_args = [
            arg for arg in cmd if arg.startswith("openai_base_url=")
        ]
        assert base_url_args
        assert "trace_id=" not in base_url_args[0]

    def test_command_via_registry(
        self, tmp_workdir, openai_model
    ) -> None:
        """The registry-built adapter produces the same command shape."""
        adapter = create_adapter(
            "codex",
            workdir=str(tmp_workdir),
            model=openai_model,
            gateway_url="http://localhost:9999",
            trace_id="reg-cell",
        )
        assert adapter is not None
        cmd = adapter.get_command("do work")
        base_url_args = [
            arg for arg in cmd if arg.startswith("openai_base_url=")
        ]
        assert base_url_args
        assert "trace_id=reg-cell" in base_url_args[0]
