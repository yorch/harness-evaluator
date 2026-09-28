"""Tests for the Docker runner's run narration.

The runner used to emit no INFO logging at all: a cell that took ten minutes
produced not one line about where the time went, and a harness that exited
non-zero (bad credentials, missing binary, crash) looked exactly like one
that ran fine and changed nothing.
"""

from __future__ import annotations

import inspect
import logging
import re
from unittest.mock import MagicMock

from harness_evaluator.adapters.base import AdapterResult
from harness_evaluator.orchestrator.config import (
    AuthMode,
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.runner import docker as docker_module
from harness_evaluator.runner.docker import (
    PHASE_DESCRIPTIONS,
    DockerRunner,
    _excerpt,
    describe_phase,
)

LOGGER = "harness_evaluator.runner.docker"


def _cell(auth_mode: AuthMode = AuthMode.API_KEY) -> RunCell:
    return RunCell(
        run_name="r",
        harness=HarnessSpec(name="h", adapter="opencode"),
        model=ModelSpec(
            name="m", provider="anthropic", api_key_env="X", auth_mode=auth_mode
        ),
        task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
        repeat=0,
    )


def _runner(tmp_path) -> DockerRunner:
    return DockerRunner(
        image="img",
        workdir_base=str(tmp_path / "wd"),
        results_db=str(tmp_path / "results.db"),
        gateway_db=str(tmp_path / "gateway.db"),
    )


class TestDescribePhase:
    def test_every_phase_the_runner_writes_has_a_description(self) -> None:
        """Scan the real ``_set_phase`` call sites, so adding or renaming a
        phase without a description fails here instead of showing up in the
        log as a bare identifier."""
        source = inspect.getsource(docker_module)
        phases = set(re.findall(r'_set_phase\(cell, f?"([a-z_]+)', source))
        assert phases, "no _set_phase call sites found"
        missing = phases - set(PHASE_DESCRIPTIONS)
        assert not missing, f"phases without a description: {missing}"

    def test_multi_phase_suffix_is_resolved(self) -> None:
        described = describe_phase("harness_running:review")
        assert PHASE_DESCRIPTIONS["harness_running"] in described
        assert "review" in described

    def test_unknown_phase_falls_back_to_itself(self) -> None:
        assert describe_phase("something_new") == "something_new"


class TestSetPhaseLogs:
    def test_open_ended_evaluation_names_the_judge(self, tmp_path, caplog) -> None:
        runner = _runner(tmp_path)
        cell = _cell()
        cell.task.track = TaskTrack.OPEN_ENDED
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._set_phase(cell, "evaluating")
        assert any("LLM judge" in r.getMessage() for r in caplog.records)
        assert not any("hidden tests" in r.getMessage() for r in caplog.records)

    def test_phase_transition_is_logged_at_info(self, tmp_path, caplog) -> None:
        """The results store is only polled by the TUI, so without this log
        every other run mode is silent for the whole body of a cell."""
        runner = _runner(tmp_path)
        cell = _cell()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._set_phase(cell, "harness_running")
        messages = [r.getMessage() for r in caplog.records]
        assert any(cell.cell_id in m and "running the harness" in m for m in messages)

    def test_a_store_failure_does_not_prevent_the_log(self, tmp_path, caplog) -> None:
        runner = _runner(tmp_path)
        runner._phase_store = MagicMock()
        runner._phase_store.set_cell_phase.side_effect = RuntimeError("locked")
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._set_phase(_cell(), "evaluating")
        assert any("evaluating the result" in r.getMessage() for r in caplog.records)


class TestHarnessExitLogging:
    def test_clean_exit_logged_at_info(self, tmp_path, caplog) -> None:
        runner = _runner(tmp_path)
        result = AdapterResult(
            exit_code=0, stdout="ok", stderr="", timed_out=False, duration_ms=1500.0
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._log_harness_exit(_cell(), result, timeout=300)
        assert any("harness exited 0 after 1.5s" in r.getMessage() for r in caplog.records)

    def test_nonzero_exit_logged_at_warning_with_output_tail(
        self, tmp_path, caplog
    ) -> None:
        runner = _runner(tmp_path)
        result = AdapterResult(
            exit_code=1,
            stdout="",
            stderr="\x1b[91mError: API key is invalid\x1b[0m",
            timed_out=False,
            duration_ms=900.0,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._log_harness_exit(_cell(), result, timeout=300)
        records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert records, "a non-zero harness exit must be a warning, not an info line"
        message = records[0].getMessage()
        assert "harness exited 1" in message
        assert "Error: API key is invalid" in message
        # ANSI escapes must never reach the log: they would repaint the
        # terminal (and the TUI's log pane).
        assert "\x1b" not in message

    def test_timeout_logged_at_warning(self, tmp_path, caplog) -> None:
        runner = _runner(tmp_path)
        result = AdapterResult(
            exit_code=-1, stdout="", stderr="", timed_out=True, duration_ms=300_000.0
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._log_harness_exit(_cell(), result, timeout=300)
        assert any("TIMED OUT after 300s" in r.getMessage() for r in caplog.records)

    def test_phase_name_included_for_multi_phase(self, tmp_path, caplog) -> None:
        runner = _runner(tmp_path)
        result = AdapterResult(
            exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=10.0
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            runner._log_harness_exit(_cell(), result, timeout=60, phase="review")
        assert any("phase review" in r.getMessage() for r in caplog.records)


class TestExcerpt:
    def test_redacts_secrets(self) -> None:
        assert "sk-ant-api03-SECRETVALUE" not in _excerpt(
            "boom with key sk-ant-api03-SECRETVALUE1234567890"
        )

    def test_collapses_newlines(self) -> None:
        assert "\n" not in _excerpt("line one\nline two\nline three")

    def test_empty_input_is_labelled(self) -> None:
        assert _excerpt("") == "(no output)"

    def test_bounded(self) -> None:
        assert len(_excerpt("x " * 5000, max_chars=100)) <= 101


class TestExpectsGatewayCalls:
    """Zero captured calls is only a fault for a harness that was handed the
    gateway URL and is known to honour it. Derived from what each real
    adapter produces, not from the auth mode or provider."""

    GW = "http://host.docker.internal:8877"

    def _expects(self, adapter_name: str, model: ModelSpec, monkeypatch) -> bool:
        from harness_evaluator.adapters.registry import create_adapter

        monkeypatch.setenv("X", "sk-test")
        adapter = create_adapter(
            name=adapter_name,
            workdir="/tmp/wd",
            model=model,
            gateway_url=self.GW,
            trace_id="cell",
        )
        assert adapter is not None
        env = adapter.get_env()
        cmd = adapter.get_command("do the task")
        return DockerRunner._expects_gateway_calls(adapter, self.GW, env, cmd)

    def test_opencode_anthropic_api_key_is_expected(self, monkeypatch) -> None:
        model = ModelSpec(name="m", provider="anthropic", api_key_env="X")
        assert self._expects("opencode", model, monkeypatch)

    def test_codex_passes_the_gateway_on_argv(self, monkeypatch) -> None:
        model = ModelSpec(name="m", provider="openai", api_key_env="X")
        assert self._expects("codex", model, monkeypatch)

    def test_codex_chatgpt_routes_via_the_codex_path(self, monkeypatch) -> None:
        """ChatGPT auth is routed through the gateway's /codex path on argv
        (chatgpt_base_url), not by a base-URL env var."""
        model = ModelSpec(
            name="m",
            provider="openai",
            api_key_env="X",
            auth_mode=AuthMode.CODEX_CHATGPT,
        )
        assert self._expects("codex", model, monkeypatch)

    def test_gemini_google_model_is_not_expected(self, monkeypatch) -> None:
        model = ModelSpec(name="m", provider="google", api_key_env="X")
        assert not self._expects("gemini", model, monkeypatch)

    def test_backend_owning_adapters_are_not_expected(self, monkeypatch) -> None:
        model = ModelSpec(name="m", provider="anthropic", api_key_env="X")
        for name in ("cursor", "copilot", "kiro"):
            assert not self._expects(name, model, monkeypatch), name

    def test_minimal_tier_adapters_are_not_expected(self, monkeypatch) -> None:
        """Pi and OMP are handed the base URL but may ignore it."""
        model = ModelSpec(name="m", provider="anthropic", api_key_env="X")
        for name in ("pi", "omp"):
            assert not self._expects(name, model, monkeypatch), name

    def test_no_gateway_url_is_not_expected(self) -> None:
        adapter = MagicMock()
        adapter.info.return_value.observability_tier = "full"
        assert not DockerRunner._expects_gateway_calls(adapter, "", {}, [])
