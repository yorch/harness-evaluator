"""Tests for `harness-evaluator run`'s progress-mode selection and summary.

Covers cli.py findings F2 (TUI/Live/plain mode selection is unreachable dead
code), F3 (`progress` may be `None` and is dereferenced), F4 (`-v`/`-vv` is
ignored in TUI mode), F-UI (total_cost/budget conflation in the summary and
progress panel), the round-1 adversarial-review fixes (fallback keyed on
"the TUI produced no result" rather than "the TUI raised", a shutdown-phase
failure after the worker completed must not re-run the orchestrator, an
unknown outcome must exit non-zero, billable-cost truthfulness), and the
round-2 re-review fixes: `_configure_logging` must be re-applied after the
TUI attempt (Textual's own on_unmount can otherwise leave root logging on a
bare stderr StreamHandler at WARNING for the rest of the run), the fallback
must be gated on `tui_app.return_code != 0` (not `progress_result is None`
alone) so a TUI that exits cleanly without ever setting a result does not
re-run the whole matrix with real spend, and a mid-run Textual panic that
still produced a partial result must report the run as interrupted and
exit non-zero rather than reading as a clean success.
"""

from __future__ import annotations

import asyncio
from unittest import mock

import typer.testing as tt
import yaml
from textual.app import App
from typer.testing import CliRunner

from harness_evaluator.cli import _render_progress_panel, app
from harness_evaluator.orchestrator.config import CostMode, RunConfig
from harness_evaluator.orchestrator.engine import OrchestratorProgress
from harness_evaluator.orchestrator.results_store import ResultsStore

runner = CliRunner()

ORCH_TARGET = "harness_evaluator.orchestrator.engine.Orchestrator"
DOCKER_TARGET = "harness_evaluator.runner.docker.DockerRunner"


def _write_config(
    tmp_path,
    *,
    budget_usd: float | None = None,
    name: str = "cli-run-test",
    extra_models: list[dict[str, object]] | None = None,
):
    """Write a minimal one-cell run config + task library under tmp_path."""
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    (task_dir / "task1.yaml").write_text(
        yaml.dump(
            {
                "tasks": [
                    {
                        "id": "t1",
                        "name": "Task 1",
                        "track": "swe",
                        "task_prompt": "Fix it",
                        "test_command": "true",
                    }
                ]
            }
        )
    )
    models: list[dict[str, object]] = [{"name": "m", "provider": "anthropic", "api_key_env": "X"}]
    if extra_models:
        models.extend(extra_models)
    config_data: dict[str, object] = {
        "name": name,
        "harnesses": [{"name": "h", "adapter": "opencode"}],
        "models": models,
        "tasks": ["*"],
        "task_library_path": str(task_dir),
        "repeats": 1,
        "results_db": str(tmp_path / "results.db"),
        "gateway_db": str(tmp_path / "gateway.db"),
        "workdir": str(tmp_path / "workdir"),
    }
    if budget_usd is not None:
        config_data["budget_usd"] = budget_usd
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))
    return config_file


def _invoke(args: list[str], *, isatty: bool):
    """Invoke the CLI with sys.stdout.isatty() forced to `isatty`.

    Typer's CliRunner swaps in a fresh `_NamedTextIOWrapper` for the
    duration of invoke(), so patching the pre-invoke `sys.stdout` object has
    no effect — the wrapper class itself must be patched.
    """
    with mock.patch.object(tt._NamedTextIOWrapper, "isatty", lambda self: isatty):
        return runner.invoke(app, args)


def _fake_progress(**overrides: object) -> OrchestratorProgress:
    defaults: dict[str, object] = {
        "total_cells": 1,
        "completed": 1,
        "failed": 0,
        "skipped": 0,
        "total_cost": 1.23,
    }
    defaults.update(overrides)
    return OrchestratorProgress(**defaults)  # type: ignore[arg-type]


class _CountingOrchestrator:
    """Stands in for Orchestrator; counts constructions and run() awaits.

    `harness_evaluator.orchestrator.engine.Orchestrator` is the shared
    lazy-import site used by BOTH cli.py's `run()` and tui/app.py's
    `_run_eval` worker, so a plain `unittest.mock.Mock` patched there
    cannot tell "the TUI's own internal run" apart from "cli.py's fallback
    run" -- both increment the same call/await counters. An explicit,
    independent counter can, which is what the real-Textual tests below
    need to prove the orchestrator ran exactly as many times as expected.
    """

    counts: dict[str, int] = {"constructed": 0, "run_awaited": 0}
    result: OrchestratorProgress | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).counts["constructed"] += 1
        self.progress = OrchestratorProgress(total_cells=1)
        self._on_progress = kwargs.get("on_progress")

    async def run(self) -> OrchestratorProgress | None:
        type(self).counts["run_awaited"] += 1
        await asyncio.sleep(0)
        res = type(self).result
        if self._on_progress is not None and res is not None:
            self._on_progress(res)
        return res

    @classmethod
    def reset(cls, result: OrchestratorProgress | None = None) -> None:
        cls.counts = {"constructed": 0, "run_awaited": 0}
        cls.result = result


class TestProgressModeSelection:
    """F2: exactly one live-UI mode is selected, and each is reachable.

    The fallback predicate is "the TUI produced no result", not "the TUI
    raised" — see TestRealTextualIntegration for the real-Textual proof of
    both directions of that distinction.
    """

    def test_no_progress_flag_runs_plainly_even_on_a_tty(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                [
                    "run",
                    str(config_file),
                    "--no-check-gateway",
                    "--no-progress",
                ],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        mock_evalapp.assert_not_called()
        mock_orch.return_value.run.assert_awaited_once()
        # Plain mode passes no on_progress callback.
        assert mock_orch.call_args.kwargs.get("on_progress") is None

    def test_non_tty_runs_plainly_even_with_progress_flag(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        mock_evalapp.assert_not_called()
        mock_orch.return_value.run.assert_awaited_once()

    def test_no_tui_flag_uses_live_panel_on_a_tty(self, tmp_path) -> None:
        """--no-tui skips Textual entirely but, unlike --no-progress, still
        wants a live display on a TTY -- it should route straight to the
        same Rich Live panel the TUI falls back to on failure, not to the
        plain path."""
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("rich.live.Live"),
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-tui"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        mock_evalapp.assert_not_called()
        mock_orch.return_value.run.assert_awaited_once()
        # Live-panel mode passes an on_progress callback (plain mode does not).
        assert mock_orch.call_args.kwargs.get("on_progress") is not None
        assert "Run complete" in result.output

    def test_no_tui_flag_runs_plainly_on_non_tty(self, tmp_path) -> None:
        """--no-tui on a non-TTY has nothing to add over the existing
        non-TTY behaviour -- still plain, no live panel."""
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-tui"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        mock_evalapp.assert_not_called()
        mock_orch.return_value.run.assert_awaited_once()
        assert mock_orch.call_args.kwargs.get("on_progress") is None

    def test_tty_with_progress_uses_the_textual_tui(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_evalapp.return_value.result = _fake_progress()
            mock_evalapp.return_value.return_code = 0
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        mock_evalapp.assert_called_once()
        mock_evalapp.return_value.run.assert_called_once()
        # The TUI ran the orchestrator itself; cli.py must not run it again.
        mock_orch.assert_not_called()

    def test_tui_exception_with_no_salvageable_result_falls_back_and_runs_once(
        self, tmp_path
    ) -> None:
        """`EvalApp.run()` raises and no result was ever set: fall back."""
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("rich.live.Live"),
        ):
            mock_evalapp.return_value.result = None
            mock_evalapp.return_value.return_code = 1
            mock_evalapp.return_value.run.side_effect = RuntimeError("no terminal driver")
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        mock_evalapp.assert_called_once()
        # Fallback path must run the orchestrator exactly once, not twice.
        mock_orch.return_value.run.assert_awaited_once()

    def test_tui_clean_none_result_falls_back_and_runs_once(self, tmp_path) -> None:
        """`EvalApp.run()` returns normally (no exception) but with no result
        — the shape of a real Textual startup panic. Must still fall back
        and run exactly once, not print a silent zero-cell "success"."""
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("rich.live.Live"),
        ):
            mock_evalapp.return_value.result = None
            mock_evalapp.return_value.return_code = 1
            mock_orch.return_value.run = mock.AsyncMock(
                return_value=_fake_progress(completed=1, total_cost=4.0)
            )
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        mock_orch.return_value.run.assert_awaited_once()
        # The real fallback progress is shown, not a fabricated zeroed one.
        assert "Passed: 1" in result.output
        assert "$4.0000" in result.output

    def test_tui_exception_with_salvaged_result_does_not_rerun(self, tmp_path) -> None:
        """`EvalApp.run()` raises AFTER the worker already set a result (a
        shutdown-phase failure): the salvaged result must be used, and the
        orchestrator must NOT be re-entered (no double run)."""
        config_file = _write_config(tmp_path)
        completed_progress = _fake_progress(completed=8, total_cells=8, total_cost=6.0)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_evalapp.return_value.result = completed_progress
            mock_evalapp.return_value.return_code = 0
            mock_evalapp.return_value.run.side_effect = RuntimeError("shutdown exploded")
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        mock_orch.assert_not_called()
        assert "Passed: 8" in result.output
        assert "$6.0000" in result.output


class TestRealTextualIntegration:
    """The mock-shaped tests above cannot observe what real Textual does on
    a startup failure (it panics internally and App.run() returns None
    rather than raising — see the adversarial review's t_textual.py probe)
    or what `tui_app.return_code` is in each shape. These tests construct a
    REAL EvalApp and drive real Textual internals, using
    `_CountingOrchestrator` (not a shared Mock) so run counts are counted,
    not inferred. Rows refer to the re-review's mode truth table."""

    def test_real_textual_startup_failure_falls_back_and_runs_exactly_once(
        self, tmp_path
    ) -> None:
        """Row 5a: App._build_driver raises -> App.run() returns None
        without raising, return_code 1 -> fall back, run exactly once."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(_fake_progress(completed=1, total_cost=2.5))
        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _CountingOrchestrator),
            mock.patch.object(
                App, "_build_driver", side_effect=RuntimeError("no usable terminal")
            ),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert result.exit_code == 0, result.output
        assert _CountingOrchestrator.counts == {"constructed": 1, "run_awaited": 1}
        assert "Run outcome UNKNOWN" not in result.output
        assert "Passed: 1" in result.output
        assert "$2.5000" in result.output

    def test_real_textual_startup_failure_on_mount_raises_falls_back(self, tmp_path) -> None:
        """Row 5c: EvalApp.on_mount raises (a real function, not a bare
        Mock -- a Mock would leave the app idling forever with no exit)."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(_fake_progress(completed=1, total_cost=2.5))
        from harness_evaluator.tui import EvalApp

        def boom_mount(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("on_mount boom")

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _CountingOrchestrator),
            mock.patch.object(EvalApp, "on_mount", boom_mount),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert result.exit_code == 0, result.output
        assert _CountingOrchestrator.counts == {"constructed": 1, "run_awaited": 1}
        assert "Passed: 1" in result.output

    def test_real_textual_shutdown_failure_after_completion_does_not_rerun(
        self, tmp_path
    ) -> None:
        """Row 6: App._shutdown raises AFTER the worker completed and
        called self.exit() (return_code 0) -- salvage the result, do not
        re-run."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(_fake_progress(completed=8, total_cells=8, total_cost=6.0))
        orig_shutdown = App._shutdown

        async def boom_shutdown(self):  # type: ignore[no-untyped-def]
            await orig_shutdown(self)
            raise RuntimeError("shutdown exploded")

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _CountingOrchestrator),
            mock.patch.object(App, "_shutdown", boom_shutdown),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert result.exit_code == 0, result.output
        # Constructed and run exactly once, inside the TUI's own worker —
        # cli.py must not re-enter it after the salvaged result.
        assert _CountingOrchestrator.counts == {"constructed": 1, "run_awaited": 1}
        assert "Passed: 8" in result.output
        assert "$6.0000" in result.output

    def test_real_textual_q_pressed_mid_run_does_not_rerun(self, tmp_path) -> None:
        """Row 7: the user quits while the worker is still awaiting
        orchestrator.run(). The worker's `except asyncio.CancelledError:
        self._result = orchestrator.progress; raise` sets a non-None
        (partial) result before the app exits cleanly (return_code 0) --
        must run exactly once, not fall back."""
        config_file = _write_config(tmp_path)

        class _SlowOrchestrator(_CountingOrchestrator):
            async def run(self) -> OrchestratorProgress | None:
                type(self).counts["run_awaited"] += 1
                await asyncio.sleep(30)
                return None  # pragma: no cover - cancelled first

        _CountingOrchestrator.reset(None)
        from harness_evaluator.tui import EvalApp

        orig_mount = EvalApp.on_mount

        def on_mount(self):  # type: ignore[no-untyped-def]
            orig_mount(self)
            self.set_timer(0.3, self.exit)

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _SlowOrchestrator),
            mock.patch.object(EvalApp, "on_mount", on_mount),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert _CountingOrchestrator.counts == {
            "constructed": 1,
            "run_awaited": 1,
        }, _CountingOrchestrator.counts
        assert result.exit_code == 0, result.output

    def test_real_textual_quit_before_worker_sets_result_does_not_rerun(
        self, tmp_path
    ) -> None:
        """Row 7b (round-2 Important 2): the worker never gets a chance to
        set `_result` (patched to a genuine no-op, deterministically -- a
        same-tick race is sub-millisecond and not reliably reproducible by
        timing alone), but the app still exits CLEANLY via a timer
        (return_code 0), unlike a Textual panic. Must NOT re-run the whole
        matrix with real containers just because the user quit before an
        answer arrived."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(_fake_progress(completed=1, total_cost=9.0))
        from harness_evaluator.tui import EvalApp

        def noop_run_eval(self):  # type: ignore[no-untyped-def]
            return None

        orig_mount = EvalApp.on_mount

        def on_mount(self):  # type: ignore[no-untyped-def]
            orig_mount(self)
            self.set_timer(0.1, self.exit)

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _CountingOrchestrator),
            mock.patch.object(EvalApp, "_run_eval", noop_run_eval),
            mock.patch.object(EvalApp, "on_mount", on_mount),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert _CountingOrchestrator.counts == {
            "constructed": 0,
            "run_awaited": 0,
        }, _CountingOrchestrator.counts
        assert result.exit_code != 0
        assert "Run outcome UNKNOWN" in result.output

    def test_real_textual_worker_dies_before_try_block_does_not_rerun(self, tmp_path) -> None:
        """Row 7c: `_run_eval` raises before its try/finally (its
        `self.query_one(ProgressFooter)` call, specifically -- NOT
        `on_mount`'s own unrelated `query_one("#eval-log", ...)` call,
        which must keep working normally). `_result` stays None and the
        `finally: self.exit()` never runs, but the app still exits cleanly
        (return_code 0) once the user quits later. Must NOT re-run."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(_fake_progress(completed=1, total_cost=9.0))
        from harness_evaluator.tui import EvalApp
        from harness_evaluator.tui.widgets import ProgressFooter

        real_query_one = EvalApp.query_one

        def selective_query_one(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if args and args[0] is ProgressFooter:
                raise RuntimeError("no footer")
            return real_query_one(self, *args, **kwargs)

        orig_mount = EvalApp.on_mount

        def on_mount(self):  # type: ignore[no-untyped-def]
            orig_mount(self)
            self.set_timer(0.2, self.exit)

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _CountingOrchestrator),
            mock.patch.object(EvalApp, "on_mount", on_mount),
            mock.patch.object(EvalApp, "query_one", selective_query_one),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert _CountingOrchestrator.counts == {
            "constructed": 0,
            "run_awaited": 0,
        }, _CountingOrchestrator.counts
        assert result.exit_code != 0
        assert "Run outcome UNKNOWN" in result.output

    def test_real_textual_keyboard_interrupt_does_not_fall_back(self, tmp_path) -> None:
        """Row 9: Ctrl+C escaping App.run() is a BaseException, not caught
        by the widened `except Exception:` -- no fallback, no orchestrator
        construction, and Typer converts it to the conventional exit 130."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(_fake_progress(completed=1, total_cost=3.0))
        from harness_evaluator.tui import EvalApp

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _CountingOrchestrator),
            mock.patch.object(EvalApp, "run", side_effect=KeyboardInterrupt()),
        ):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert _CountingOrchestrator.counts == {"constructed": 0, "run_awaited": 0}
        assert result.exit_code == 130

    def test_empty_matrix_or_all_skipped_is_not_conflated_with_no_result(
        self, tmp_path
    ) -> None:
        """Row 11: a real "zero cells done" result (empty matrix, or every
        cell skipped by resumability) is non-None and must not trigger the
        fallback -- "no work to do" is not "no answer"."""
        config_file = _write_config(tmp_path)
        _CountingOrchestrator.reset(
            _fake_progress(total_cells=0, completed=0, failed=0, skipped=0, total_cost=0.0)
        )
        with mock.patch(DOCKER_TARGET), mock.patch(ORCH_TARGET, _CountingOrchestrator):
            result = _invoke(["run", str(config_file), "--no-check-gateway"], isatty=True)
        assert result.exit_code == 0, result.output
        assert _CountingOrchestrator.counts == {"constructed": 1, "run_awaited": 1}
        assert "Run outcome UNKNOWN" not in result.output


class TestMidRunPanicReporting:
    """Round-2 Minor 3 / re-review row 10, closed out in the final fix wave:
    a Textual panic mid-run, after the worker already salvaged a partial
    (non-None) result via `except asyncio.CancelledError`, must not read as
    a clean "Run complete" -- cli.py's own logic (return_code 1 + a
    non-None result -> "Run interrupted", exit non-zero) is exercised here
    end-to-end against REAL Textual internals, not a mocked EvalApp.

    This drives a real `_handle_exception(...)` call while the worker's
    `orchestrator.run()` is still mid-`await`, using `_CountingOrchestrator`
    (not a shared Mock) so the "did cli.py re-run the matrix" question is
    counted, not inferred -- same pattern as TestRealTextualIntegration.
    `tui/app.py`'s `_run_eval` `finally` clause now preserves an
    already-panicked return_code (`self.exit(return_code=self.return_code
    or 0)`) instead of unconditionally resetting it to 0, which is what
    makes this reachable at all."""

    def test_mid_run_panic_reports_interrupted_and_exits_nonzero(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)

        class _PanickyMidRunOrchestrator(_CountingOrchestrator):
            async def run(self) -> OrchestratorProgress | None:
                type(self).counts["run_awaited"] += 1
                # Simulate 3 of 8 cells already completed before the panic
                # hits -- this is what `_run_eval` salvages via `self._result
                # = orchestrator.progress` when the worker is cancelled.
                self.progress = _fake_progress(total_cells=8, completed=3, total_cost=2.0)
                await asyncio.sleep(30)
                return None  # pragma: no cover - cancelled first

        _CountingOrchestrator.reset(None)
        from harness_evaluator.tui import EvalApp

        orig_mount = EvalApp.on_mount

        def on_mount(self):  # type: ignore[no-untyped-def]
            orig_mount(self)
            self.set_timer(
                0.3, lambda: self._handle_exception(RuntimeError("simulated Textual panic"))
            )

        with (
            mock.patch(DOCKER_TARGET),
            mock.patch(ORCH_TARGET, _PanickyMidRunOrchestrator),
            mock.patch.object(EvalApp, "on_mount", on_mount),
        ):
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway"],
                isatty=True,
            )
        assert result.exit_code != 0
        assert "Run interrupted" in result.output
        assert "PARTIAL" in result.output
        assert "Passed: 3" in result.output
        assert "Run complete" not in result.output
        # A genuine mid-run panic must not be treated as "needs a fresh
        # run" either -- the partial result IS the answer, just an
        # incomplete one; re-running would be the Important-2 bug again.
        assert _CountingOrchestrator.counts == {
            "constructed": 1,
            "run_awaited": 1,
        }, _CountingOrchestrator.counts


class TestUnknownOutcomeGuard:
    """F3 / round-1 Finding 6: an outcome that is genuinely unknown (no
    snapshot even after the fallback ran) must be unmistakable — exit
    non-zero and never render as a zeroed "Run complete" summary."""

    def test_exits_nonzero_and_does_not_print_a_normal_summary(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("rich.live.Live"),
        ):
            mock_evalapp.return_value.result = None
            mock_evalapp.return_value.return_code = 1
            # Even the fallback produced nothing -- a genuinely unknown outcome.
            mock_orch.return_value.run = mock.AsyncMock(return_value=None)
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway"],
                isatty=True,
            )
        assert result.exit_code != 0
        assert "Run outcome UNKNOWN" in result.output
        assert "Run complete" not in result.output
        assert "Passed:" not in result.output


class TestVerboseWiring:
    """F4: -v/-vv must reach _configure_logging and EvalApp on every path."""

    def test_configure_logging_called_on_tui_path(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.cli._configure_logging") as mock_configure,
        ):
            mock_evalapp.return_value.result = _fake_progress()
            mock_evalapp.return_value.return_code = 0
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "-vv"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        # Called twice: once before the TUI attempt, once after it returns
        # -- unconditionally, since cli.py cannot rely on knowing whether
        # Textual's on_unmount left root logging on its bare StreamHandler
        # fallback or restored the real handlers; reconfiguring either way
        # is what makes -v/-vv reliable regardless.
        assert mock_configure.call_args_list == [mock.call(2), mock.call(2)]
        assert mock_evalapp.call_args.kwargs.get("verbose") == 2

    def test_configure_logging_called_on_plain_path(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("harness_evaluator.cli._configure_logging") as mock_configure,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "-v"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        mock_configure.assert_called_once_with(1)

    def test_configure_logging_called_on_live_fallback_path(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.tui.EvalApp") as mock_evalapp,
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("harness_evaluator.cli._configure_logging") as mock_configure,
            mock.patch("rich.live.Live"),
        ):
            mock_evalapp.return_value.result = None
            mock_evalapp.return_value.return_code = 1
            mock_evalapp.return_value.run.side_effect = RuntimeError("no terminal driver")
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "-vv"],
                isatty=True,
            )
        assert result.exit_code == 0, result.output
        # Called twice: once before the TUI attempt (so the widened
        # except's own log record is formatted), once after (so Textual's
        # on_unmount replacing root logging with a bare stderr
        # StreamHandler at WARNING does not silently swallow -v/-vv for the
        # fallback's matrix run that follows).
        assert mock_configure.call_args_list == [mock.call(2), mock.call(2)]

    def test_configure_logging_floors_to_info_when_progress_wanted_but_no_tty(
        self, tmp_path
    ) -> None:
        """Minor finding: a non-TTY run that still asked for --progress must
        not be silent at the default verbosity."""
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("harness_evaluator.cli._configure_logging") as mock_configure,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        mock_configure.assert_called_once_with(1)

    def test_configure_logging_stays_at_warning_when_no_progress_requested(
        self, tmp_path
    ) -> None:
        """The INFO floor only applies when progress was actually requested
        but impossible -- an explicit --no-progress stays quiet."""
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch("harness_evaluator.cli._configure_logging") as mock_configure,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        mock_configure.assert_called_once_with(0)


class TestCostLabelling:
    """F-UI: total_cost (informational) and the budget cap must be labelled
    as distinct quantities, not a "spent / cap" pair -- and the billable
    figure must genuinely be the billable one, not the informational total
    under a different label."""

    def test_summary_labels_total_cost_as_informational(self, tmp_path) -> None:
        config_file = _write_config(tmp_path, budget_usd=5.0)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_orch.return_value.run = mock.AsyncMock(
                return_value=_fake_progress(total_cost=12.5)
            )
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        assert "Total cost (informational" in result.output
        assert "$12.5000" in result.output
        assert "Billable cost:" in result.output
        assert "budget cap" in result.output
        # The old ambiguous "Cost: $x / $y" pairing must be gone.
        assert "Cost: $" not in result.output

    def test_summary_omits_billable_line_without_a_budget(self, tmp_path) -> None:
        config_file = _write_config(tmp_path)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            mock_orch.return_value.run = mock.AsyncMock(return_value=_fake_progress())
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        assert "Total cost (informational" in result.output
        assert "Billable cost:" not in result.output

    def test_summary_billable_cost_is_the_real_store_figure_and_differs_from_total(
        self, tmp_path
    ) -> None:
        """Kills the `get_billable_cost -> progress.total_cost` mutation: an
        empty store makes both figures $0.0000 and this distinction
        untestable, so seed one platform-cost row and one subscription-cost
        row and assert the two printed numbers genuinely differ."""
        config_file = _write_config(
            tmp_path,
            budget_usd=100.0,
            extra_models=[
                {
                    "name": "sub-model",
                    "provider": "anthropic",
                    "api_key_env": "X",
                    "cost_mode": "subscription",
                }
            ],
        )
        cfg = RunConfig.from_yaml(config_file)
        cells = cfg.build_matrix()
        assert {c.model.cost_mode for c in cells} == {CostMode.PLATFORM, CostMode.SUBSCRIPTION}
        store = ResultsStore(cfg.results_db)
        for cell in cells:
            cost = 7.0 if cell.model.cost_mode == CostMode.SUBSCRIPTION else 3.0
            store.save_result(cell, "success", 1.0, total_cost=cost)

        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
        ):
            # The informational total is both cells; the persisted store
            # (via get_billable_cost) is what the "billable" line must show.
            mock_orch.return_value.run = mock.AsyncMock(
                return_value=_fake_progress(total_cost=10.0)
            )
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        assert "Total cost (informational" in result.output
        assert "$10.0000" in result.output
        assert "Billable cost: $3.0000 / $100.00 budget cap" in result.output

    def test_summary_survives_billable_cost_lookup_failure(self, tmp_path) -> None:
        """Minor finding: a transient store error (e.g. `database is locked`
        under parallel_runs > 1, per engine.py's identical guard) must not
        abort the summary or exit non-zero on an otherwise-successful run."""
        config_file = _write_config(tmp_path, budget_usd=5.0)
        with (
            mock.patch("harness_evaluator.runner.docker.DockerRunner"),
            mock.patch("harness_evaluator.orchestrator.engine.Orchestrator") as mock_orch,
            mock.patch(
                "harness_evaluator.orchestrator.results_store.ResultsStore.get_billable_cost",
                side_effect=RuntimeError("database is locked"),
            ),
        ):
            mock_orch.return_value.run = mock.AsyncMock(
                return_value=_fake_progress(total_cost=1.0)
            )
            result = _invoke(
                ["run", str(config_file), "--no-check-gateway", "--no-progress"],
                isatty=False,
            )
        assert result.exit_code == 0, result.output
        assert "Run complete" in result.output
        assert "Billable cost:" not in result.output

    def test_render_progress_panel_labels_cost_as_informational_with_budget(self) -> None:
        panel = _render_progress_panel(_fake_progress(total_cost=9.0), start_time=0.0, budget=3.0)
        text = str(panel.renderable)
        assert "Cost (informational)" in text
        assert "Budget cap: $3.00" in text
        assert "Cost: $" not in text

    def test_render_progress_panel_labels_cost_as_informational_without_budget(self) -> None:
        panel = _render_progress_panel(_fake_progress(total_cost=9.0), start_time=0.0, budget=None)
        text = str(panel.renderable)
        assert "Cost (informational)" in text
        assert "Cost: $" not in text
