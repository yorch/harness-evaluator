"""End-to-end integration test: full pipeline.

Exercises the COMPLETE system end-to-end with no mocks of internal
components (only the external provider API is mocked):

    gateway proxy -> adapter (fake harness) -> task execution
    -> SWE evaluation -> results storage -> reporting

This proves the system actually works as a whole, not just in unit tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from heval.evaluator.swe import ErrorClass, SWEEvaluator
from heval.gateway.models import Provider
from heval.gateway.proxy import create_proxy_app
from heval.gateway.store import CallStore
from heval.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskLibrary,
)
from heval.orchestrator.results_store import ResultsStore
from heval.reporting.static_report import ReportGenerator

# Paths to the real task assets shipped with the repo.
TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
REPO_SRC = TASKS_DIR / "repos" / "swe-bugfix-001"
TASK_YAML = TASKS_DIR / "swe-bugfix-001.yaml"

# Token counts the mock upstream will report in its usage block.
MOCK_INPUT_TOKENS = 100
MOCK_OUTPUT_TOKENS = 50
MOCK_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in workdir, returning the completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )


def _init_git_repo(workdir: Path) -> None:
    """Initialise a fresh git repo in workdir and commit all files."""
    _git(workdir, "init")
    _git(workdir, "config", "user.email", "test@heval.dev")
    _git(workdir, "config", "user.name", "Heval Test")
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-m", "initial")


def _get_server_port(site: web.TCPSite) -> int:
    """Extract the actual bound port from a started TCPSite."""
    return site._server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Mock upstream provider
# ---------------------------------------------------------------------------


async def _start_mock_upstream() -> tuple[web.AppRunner, str]:
    """Start a mock Anthropic API server returning a minimal response.

    Returns (runner, base_url). The caller is responsible for cleanup.
    """

    async def handle_messages(request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response(
            {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": body.get("model", MOCK_MODEL),
                "content": [{"type": "text", "text": "I will fix the bug."}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": MOCK_INPUT_TOKENS,
                    "output_tokens": MOCK_OUTPUT_TOKENS,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            }
        )

    app = web.Application()
    app.router.add_post("/v1/messages", handle_messages)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = _get_server_port(site)
    return runner, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Gateway proxy startup
# ---------------------------------------------------------------------------


async def _start_gateway(
    upstream_url: str, db_path: str
) -> tuple[web.AppRunner, str, CallStore]:
    """Start the real gateway proxy pointing at the mock upstream.

    Returns (runner, gateway_url, store).
    """
    store = CallStore(db_path)
    app, proxy = create_proxy_app(
        store,
        upstream_overrides={Provider.ANTHROPIC: upstream_url},
        verify_ssl=False,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = _get_server_port(site)
    return runner, f"http://127.0.0.1:{port}", store


# ---------------------------------------------------------------------------
# Fake harness
# ---------------------------------------------------------------------------


async def _run_fake_harness(
    gateway_url: str,
    task_prompt: str,
    workdir: Path,
    trace_id: str,
) -> dict[str, Any]:
    """Simulate what a real harness adapter would do.

    1. Read the task prompt.
    2. Make an HTTP call to the gateway (simulating an LLM API call).
    3. Write a "fix" to the workdir (simulating harness output).

    Returns the parsed JSON response from the gateway.
    """
    request_body: dict[str, Any] = {
        "model": MOCK_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": task_prompt}],
    }

    # The harness appends trace_id as a query param so the gateway can
    # attribute this call to the current eval cell.
    url = f"{gateway_url}/v1/messages?trace_id={trace_id}"

    async with aiohttp.ClientSession() as session, session.post(
        url,
        json=request_body,
        headers={
            "x-api-key": "test-key",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    ) as resp:
        response_body = await resp.json()
        assert resp.status == 200, f"Gateway returned {resp.status}"

    # Simulate the harness writing a correct fix for the off-by-one bug.
    # The original buggy line:
    #   end = page_number * page_size - 1  # BUG: off-by-one
    # The correct line:
    #   end = page_number * page_size
    solution_path = workdir / "src" / "solution.py"
    fixed_source = (
        '"""List processing utilities with pagination support."""\n\n\n'
        "def get_page(items, page_number, page_size=10):\n"
        '    """Return a page of items from a list.\n\n'
        "    Args:\n"
        "        items: The full list of items.\n"
        "        page_number: 1-indexed page number to retrieve.\n"
        "        page_size: Number of items per page (default 10).\n\n"
        "    Returns:\n"
        "        A list containing the items for the requested page.\n"
        "        Returns an empty list if the page is beyond the available items.\n"
        '    """\n'
        "    start = (page_number - 1) * page_size\n"
        "    end = page_number * page_size  # Fixed: correct end index\n"
        "    return items[start:end]\n"
    )
    solution_path.write_text(fixed_source)

    return response_body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def task_spec() -> Any:
    """Load the real swe-bugfix-001 task from the shipped YAML."""
    lib = TaskLibrary.from_yaml(TASK_YAML)
    assert len(lib.tasks) == 1, "Expected exactly one task in swe-bugfix-001.yaml"
    return lib.tasks[0]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Copy the real swe-bugfix-001 repo into a temp workdir and init git."""
    wd = tmp_path / "workdir"
    # Copy repo contents (excluding .git so we start with a clean history).
    shutil.copytree(REPO_SRC, wd, ignore=shutil.ignore_patterns(".git"))
    _init_git_repo(wd)
    return wd


@pytest.fixture
def eval_cell(task_spec: Any) -> RunCell:
    """Build a RunCell matching what the orchestrator would create."""
    return RunCell(
        run_name="e2e-test-run",
        harness=HarnessSpec(name="fake-harness", adapter="claude-code"),
        model=ModelSpec(
            name=MOCK_MODEL,
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        task=task_spec,
        repeat=0,
    )


# ---------------------------------------------------------------------------
# The end-to-end test
# ---------------------------------------------------------------------------


async def test_full_pipeline_e2e(
    tmp_path: Path,
    task_spec: Any,
    workdir: Path,
    eval_cell: RunCell,
) -> None:
    """Exercise the full pipeline: gateway -> harness -> eval -> store -> report.

    This test starts real servers (mock upstream + gateway proxy), runs a
    fake harness that makes a real HTTP call through the gateway, evaluates
    the result with the real SWE evaluator, stores it, and generates a report.
    """
    trace_id = eval_cell.cell_id
    gateway_db = str(tmp_path / "gateway.db")
    results_db = str(tmp_path / "results.db")

    # 1. Start a mock upstream provider.
    upstream_runner, upstream_url = await _start_mock_upstream()
    try:
        # 2. Start the real gateway proxy pointing at the mock upstream.
        gateway_runner, gateway_url, call_store = await _start_gateway(
            upstream_url, gateway_db
        )
        try:
            # 3. Run the fake harness: make an API call through the gateway
            #    and write a correct fix to the workdir.
            response = await _run_fake_harness(
                gateway_url, task_spec.task_prompt, workdir, trace_id
            )

            # --- Verify the gateway returned the mock response transparently ---
            assert response["id"] == "msg_mock"
            assert response["content"][0]["text"] == "I will fix the bug."

            # 4. Verify token accounting: the gateway captured the call.
            captured_calls = call_store.get_by_trace(trace_id)
            assert len(captured_calls) == 1, (
                f"Expected 1 captured call, got {len(captured_calls)}"
            )

            captured = captured_calls[0]
            assert captured.provider == Provider.ANTHROPIC
            assert captured.model == MOCK_MODEL
            assert captured.path == "/v1/messages"
            assert captured.method == "POST"
            assert captured.response_status == 200
            assert captured.is_streaming is False
            assert captured.error is None
            assert captured.success is True

            # Token counts must match what the mock upstream returned.
            assert captured.usage.input_tokens == MOCK_INPUT_TOKENS
            assert captured.usage.output_tokens == MOCK_OUTPUT_TOKENS
            assert captured.usage.cache_read_tokens == 0
            assert captured.usage.cache_write_tokens == 0
            assert captured.usage.total_tokens == (
                MOCK_INPUT_TOKENS + MOCK_OUTPUT_TOKENS
            )

            # Sensitive headers must be redacted in storage.
            assert captured.request_headers.get("x-api-key") == "[REDACTED]"

            # Cost should be non-zero (we use a model with known pricing).
            assert captured.cost.total > 0
        finally:
            await gateway_runner.cleanup()
    finally:
        await upstream_runner.cleanup()

    # 5. Evaluate the fix with the real SWE evaluator (applies hidden tests).
    evaluator = SWEEvaluator()
    result = evaluator.evaluate(task_spec, workdir, timeout=60)

    # The fix is correct, so all hidden + visible tests should pass.
    assert result.exit_class == "pass", (
        f"Expected pass, got {result.exit_class}: {result.error_message}\n"
        f"Test output:\n{result.test_output}"
    )
    assert result.error_class == ErrorClass.SUCCESS
    assert result.success == 1.0
    assert result.tests_total > 0
    assert result.tests_passed == result.tests_total
    assert result.diff, "Diff should be non-empty after the fix"

    # 6. Store the result in a temporary results database.
    results_store = ResultsStore(results_db)
    results_store.save_result(
        cell=eval_cell,
        exit_class=result.exit_class,
        success=result.success,
        error_class=result.error_class.value,
        error_message=result.error_message,
        usage=captured.usage,
        total_cost=captured.cost.total,
        latency_ms=captured.latency_ms,
        num_api_calls=1,
        diff=result.diff,
        test_output=result.test_output,
    )

    # Verify the result was stored.
    stored = results_store.get_result(eval_cell.cell_id)
    assert stored is not None
    assert stored["exit_class"] == "pass"
    assert stored["success"] == 1.0
    assert stored["harness"] == "fake-harness"
    assert stored["model"] == MOCK_MODEL
    assert stored["task_id"] == "swe-bugfix-001"
    assert stored["num_api_calls"] == 1
    assert stored["input_tokens"] == MOCK_INPUT_TOKENS
    assert stored["output_tokens"] == MOCK_OUTPUT_TOKENS

    # 7. Generate a report and verify it contains the expected data.
    report_dir = tmp_path / "reports"
    generator = ReportGenerator(results_store)
    paths = generator.generate("e2e-test-run", report_dir)

    # All three report formats should be produced.
    assert "html" in paths
    assert "json" in paths
    assert "csv" in paths
    for fmt, path_str in paths.items():
        report_path = Path(path_str)
        assert report_path.exists(), f"{fmt} report was not created"
        assert report_path.stat().st_size > 0, f"{fmt} report is empty"

    # JSON report should contain our run data.
    json_report = json.loads(Path(paths["json"]).read_text())
    assert json_report["run_name"] == "e2e-test-run"
    assert json_report["total_cells"] == 1
    assert len(json_report["results"]) == 1
    assert json_report["results"][0]["harness"] == "fake-harness"

    # HTML report should mention the run name and harness.
    html_report = Path(paths["html"]).read_text()
    assert "e2e-test-run" in html_report
    assert "fake-harness" in html_report

    # CSV report should contain the harness name.
    csv_report = Path(paths["csv"]).read_text()
    assert "fake-harness" in csv_report

    # 8. Final sanity: the gateway DB still has the captured call.
    persisted_calls = call_store.get_all()
    assert len(persisted_calls) == 1
    assert persisted_calls[0].usage.input_tokens == MOCK_INPUT_TOKENS
