"""Tests for the Docker runner's run narration and infra-error signal.

The runner used to emit no INFO logging at all: a cell that took ten minutes
produced not one line about where the time went, and a harness that exited
non-zero (bad credentials, missing binary, crash) looked exactly like one
that ran fine and changed nothing.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from harness_evaluator.adapters.base import AdapterResult
from harness_evaluator.orchestrator.config import (
    AuthMode,
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
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
    def test_known_phases_have_descriptions(self) -> None:
        # Every phase the runner writes must be describable; an unlabelled
        # phase would show up in the log as a bare identifier.
        for phase, text in PHASE_DESCRIPTIONS.items():
            assert describe_phase(phase) == text

    def test_multi_phase_suffix_is_resolved(self) -> None:
        described = describe_phase("harness_running:review")
        assert PHASE_DESCRIPTIONS["harness_running"] in described
        assert "review" in described

    def test_unknown_phase_falls_back_to_itself(self) -> None:
        assert describe_phase("something_new") == "something_new"


class TestSetPhaseLogs:
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


class TestHostGatewayUrl:
    """The open-ended judge runs on the host, so it must never be handed the
    container-facing gateway host. It was, and every open-ended cell failed
    with ``[Errno -2] Name or service not known`` after the harness had
    already been paid for."""

    def test_unresolvable_container_host_falls_back_to_the_bridge_ip(
        self, tmp_path
    ) -> None:
        runner = _runner(tmp_path)
        runner.gateway_host = "host.docker.internal"
        runner.gateway_port = 8877
        with (
            patch(
                "harness_evaluator.runner.docker.socket.getaddrinfo",
                side_effect=OSError("Name or service not known"),
            ),
            patch(
                "harness_evaluator.gateway.network.resolve_gateway_host",
                return_value="172.17.0.1",
            ),
        ):
            assert runner.host_gateway_url() == "http://172.17.0.1:8877"

    def test_a_resolvable_host_is_used_as_is(self, tmp_path) -> None:
        runner = _runner(tmp_path)
        runner.gateway_host = "127.0.0.1"
        runner.gateway_port = 9999
        assert runner.host_gateway_url() == "http://127.0.0.1:9999"

    def test_all_interfaces_bind_becomes_loopback(self, tmp_path) -> None:
        """resolve_gateway_host can return a bind address (0.0.0.0) that is
        not a valid destination."""
        runner = _runner(tmp_path)
        runner.gateway_host = "host.docker.internal"
        with (
            patch(
                "harness_evaluator.runner.docker.socket.getaddrinfo",
                side_effect=OSError("nope"),
            ),
            patch(
                "harness_evaluator.gateway.network.resolve_gateway_host",
                return_value="0.0.0.0",
            ),
        ):
            assert runner.host_gateway_url().startswith("http://127.0.0.1:")

    def test_ipv6_is_bracketed(self, tmp_path) -> None:
        runner = _runner(tmp_path)
        runner.gateway_host = "host.docker.internal"
        with (
            patch(
                "harness_evaluator.runner.docker.socket.getaddrinfo",
                side_effect=OSError("nope"),
            ),
            patch(
                "harness_evaluator.gateway.network.resolve_gateway_host",
                return_value="::1",
            ),
        ):
            assert "[::1]" in runner.host_gateway_url()

    def test_result_is_cached(self, tmp_path) -> None:
        """Resolution shells out to `docker network inspect`; it must not run
        once per open-ended cell."""
        runner = _runner(tmp_path)
        runner.gateway_host = "host.docker.internal"
        with (
            patch(
                "harness_evaluator.runner.docker.socket.getaddrinfo",
                side_effect=OSError("nope"),
            ),
            patch(
                "harness_evaluator.gateway.network.resolve_gateway_host",
                return_value="172.17.0.1",
            ) as mock_resolve,
        ):
            first = runner.host_gateway_url()
            second = runner.host_gateway_url()
        assert first == second
        assert mock_resolve.call_count == 1


class TestRoutesThroughGateway:
    def test_api_key_mode_routes_through_gateway(self) -> None:
        assert DockerRunner._routes_through_gateway(_cell(AuthMode.API_KEY))

    def test_claude_oauth_routes_through_gateway(self) -> None:
        assert DockerRunner._routes_through_gateway(_cell(AuthMode.CLAUDE_OAUTH))

    def test_codex_chatgpt_does_not(self) -> None:
        """codex_chatgpt deliberately talks to the provider directly (see
        BaseAdapter.get_env), so zero captured calls is normal there and must
        not be reported as an infrastructure error."""
        assert not DockerRunner._routes_through_gateway(_cell(AuthMode.CODEX_CHATGPT))
