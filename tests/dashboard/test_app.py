"""Tests for the FastAPI dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_evaluator.dashboard.app import create_app
from harness_evaluator.orchestrator.config import (
    HarnessSpec,
    ModelSpec,
    PhaseSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.orchestrator.results_store import ResultsStore


@pytest.fixture
def store_with_results(tmp_path):
    """Create a results store with sample data."""
    store = ResultsStore(str(tmp_path / "test_results.db"))

    for harness_name in ["opencode", "claude-code"]:
        for task_id in ["task-1", "task-2"]:
            for repeat in range(2):
                cell = RunCell(
                    run_name="test-run",
                    harness=HarnessSpec(name=harness_name, adapter=harness_name),
                    model=ModelSpec(
                        name="claude-sonnet-4-20250514",
                        provider="anthropic",
                        api_key_env="X",
                    ),
                    task=TaskSpec(
                        id=task_id,
                        name=f"Task {task_id}",
                        track=TaskTrack.SWE,
                        task_prompt="Fix bug",
                    ),
                    repeat=repeat,
                )
                store.save_result(
                    cell=cell,
                    exit_class="pass" if harness_name == "opencode" else "fail",
                    success=1.0 if harness_name == "opencode" else 0.0,
                    total_cost=0.001 if harness_name == "opencode" else 0.002,
                    latency_ms=5000 if harness_name == "opencode" else 10000,
                    num_api_calls=3,
                )

    return str(tmp_path / "test_results.db")


@pytest.fixture
def client(store_with_results):
    """Create a test client for the dashboard."""
    app = create_app(results_db=store_with_results)
    return TestClient(app)


class TestDashboardHome:
    def test_home_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "harness-evaluator Dashboard" in resp.text

    def test_home_shows_runs(self, client):
        resp = client.get("/")
        assert "test-run" in resp.text

    def test_home_xss_safe(self, tmp_path):
        """Test that run names with HTML are escaped."""
        db_path = str(tmp_path / "xss_test.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name='<script>alert("xss")</script>',
            harness=HarnessSpec(name="opencode", adapter="opencode"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="pass", success=1.0,
            total_cost=0, latency_ms=100, num_api_calls=1,
        )

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/")
        # The script tag should be escaped, not rendered
        assert "<script>alert" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestDashboardRunDetail:
    def test_run_detail_returns_html(self, client):
        resp = client.get("/run/test-run")
        assert resp.status_code == 200
        assert "test-run" in resp.text
        assert "opencode" in resp.text
        assert "claude-code" in resp.text

    def test_run_detail_404_for_unknown(self, client):
        resp = client.get("/run/nonexistent")
        assert resp.status_code == 404

    def test_run_detail_with_model_filter(self, client):
        resp = client.get("/run/test-run", params={"model": "claude-sonnet-4-20250514"})
        assert resp.status_code == 200
        assert "claude-sonnet-4-20250514" in resp.text

    def test_run_detail_with_harness_filter(self, client):
        resp = client.get("/run/test-run", params={"harness": "opencode"})
        assert resp.status_code == 200
        assert "opencode" in resp.text

    def test_run_detail_pagination(self, client):
        """Test that pagination works."""
        resp = client.get("/run/test-run", params={"per_page": 2, "page": 1})
        assert resp.status_code == 200
        # Should show pagination controls
        assert "pagination" in resp.text.lower() or "Next" in resp.text

    def test_run_detail_xss_safe(self, tmp_path):
        """Test that run names with HTML are escaped in detail page."""
        db_path = str(tmp_path / "xss_detail.db")
        store = ResultsStore(db_path)
        # Use a payload without slashes (which break URL paths)
        run_name = '<img src=x onerror=alert(1)>'
        cell = RunCell(
            run_name=run_name,
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="pass", success=1.0,
            total_cost=0, latency_ms=100, num_api_calls=1,
        )

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get(f"/run/{run_name}")
        assert resp.status_code == 200
        # The angle brackets should be escaped, preventing XSS
        assert "<img src=x onerror" not in resp.text
        assert "&lt;img" in resp.text


class TestDashboardAPI:
    def test_api_list_runs(self, client):
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert len(data["runs"]) == 1
        assert data["runs"][0]["run_name"] == "test-run"
        assert data["runs"][0]["total_cells"] == 8

    def test_api_run_results(self, client):
        resp = client.get("/api/run/test-run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_name"] == "test-run"
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "results" in data

    def test_api_run_results_with_filter(self, client):
        resp = client.get("/api/run/test-run", params={"harness": "opencode"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4  # Only opencode results
        assert all(r["harness"] == "opencode" for r in data["results"])

    def test_api_run_results_pagination(self, client):
        resp = client.get("/api/run/test-run", params={"per_page": 3, "page": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["per_page"] == 3
        assert data["page"] == 1
        assert len(data["results"]) <= 3
        assert data["total"] == 8

    def test_api_run_results_404(self, client):
        resp = client.get("/api/run/nonexistent")
        assert resp.status_code == 404

    def test_api_leaderboard(self, client):
        resp = client.get("/api/run/test-run/leaderboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "leaderboards" in data
        assert "claude-sonnet-4-20250514" in data["leaderboards"]

    def test_api_leaderboard_404(self, client):
        resp = client.get("/api/run/nonexistent/leaderboard")
        assert resp.status_code == 404

    def test_api_run_status(self, client):
        """Test the live status endpoint."""
        resp = client.get("/api/run/test-run/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_name"] == "test-run"
        assert "state" in data

    def test_api_run_status_404(self, client):
        resp = client.get("/api/run/nonexistent/status")
        assert resp.status_code == 404

    def test_api_run_errors(self, store_with_results):
        """Test the errors endpoint returns failed/skipped cells."""
        db_path = store_with_results
        store = ResultsStore(db_path)
        store.set_cell_state("test-cell-fail", "test-run", "failed", "Docker timeout")
        store.set_cell_state("test-cell-skip", "test-run", "skipped", "Budget cap")

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/api/run/test-run/errors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_name"] == "test-run"
        assert "failed_cells" in data
        cell_ids = {c["cell_id"] for c in data["failed_cells"]}
        assert "test-cell-fail" in cell_ids
        assert "test-cell-skip" in cell_ids
        # Should also include run_results cells with exit_class != 'pass'
        # (the fixture has claude-code cells with exit_class='fail')
        fail_cell = next(c for c in data["failed_cells"] if c["cell_id"] == "test-cell-fail")
        assert fail_cell["error"] == "Docker timeout"
        assert fail_cell["status"] == "failed"

    def test_api_run_errors_404(self, client):
        resp = client.get("/api/run/nonexistent/errors")
        assert resp.status_code == 404

    def test_run_detail_shows_error_message(self, tmp_path):
        """Test that error_message is rendered in the run detail page."""
        db_path = str(tmp_path / "error_test.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="err-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="fail", success=0.0,
            error_class="crash", error_message="Segmentation fault in harness",
            total_cost=0, latency_ms=100, num_api_calls=1,
        )

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/run/err-run")
        assert resp.status_code == 200
        assert "Segmentation fault in harness" in resp.text
        assert "crash" in resp.text

    def test_run_detail_shows_failed_cells_section(self, tmp_path):
        """Test that failed/skipped cells from run_state are shown."""
        db_path = str(tmp_path / "failed_test.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="fail-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="pass", success=1.0,
            total_cost=0, latency_ms=100, num_api_calls=1,
        )
        store.set_cell_state("cell-xyz", "fail-run", "failed", "OOM killed")

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/run/fail-run")
        assert resp.status_code == 200
        assert "Failed / Skipped Cells" in resp.text
        assert "cell-xyz" in resp.text
        assert "OOM killed" in resp.text

    def test_run_detail_shows_phase_results(self, tmp_path):
        """Test that phase results are rendered for multi-phase cells."""
        db_path = str(tmp_path / "phase_test.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="phase-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="pass", success=1.0,
            total_cost=0, latency_ms=100, num_api_calls=1,
        )
        store.save_phase_results(
            cell_id=cell.cell_id,
            run_name="phase-run",
            phases=[
                {
                    "name": "plan",
                    "trace_id": "tr-1",
                    "model": "m",
                    "model_role": "planning",
                    "exit_code": 0,
                    "duration_ms": 5000,
                    "timed_out": False,
                    "usage": None,
                    "total_cost": 0.01,
                    "num_api_calls": 2,
                    "error": None,
                },
                {
                    "name": "implement",
                    "trace_id": "tr-2",
                    "model": "m",
                    "model_role": "implementation",
                    "exit_code": 1,
                    "duration_ms": 10000,
                    "timed_out": True,
                    "usage": None,
                    "total_cost": 0.02,
                    "num_api_calls": 5,
                    "error": "Phase timed out",
                },
            ],
        )

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/run/phase-run")
        assert resp.status_code == 200
        assert "Phase Details" in resp.text
        assert "plan" in resp.text
        assert "implement" in resp.text
        assert "Phase timed out" in resp.text

    def test_run_detail_error_message_xss_safe(self, tmp_path):
        """error_message with HTML payloads must be escaped in the dashboard."""
        db_path = str(tmp_path / "xss_err.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="xss-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        xss_payload = '"><script>alert(1)</script>'
        store.save_result(
            cell=cell, exit_class="fail", success=0.0,
            error_class="crash", error_message=xss_payload,
            total_cost=0, latency_ms=100, num_api_calls=1,
        )
        # Also test run_state.error XSS
        store.set_cell_state("xss-cell", "xss-run", "failed", xss_payload)

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/run/xss-run")
        assert resp.status_code == 200
        # The raw script tag must not appear
        assert "<script>alert(1)</script>" not in resp.text
        # The escaped version should be present
        assert "&lt;script&gt;" in resp.text

    def test_run_detail_phase_error_xss_safe(self, tmp_path):
        """phase_results.error with HTML payloads must be escaped."""
        db_path = str(tmp_path / "xss_phase.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="xss-phase-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell, exit_class="pass", success=1.0,
            total_cost=0, latency_ms=100, num_api_calls=1,
        )
        xss_payload = '"><script>alert(99)</script>'
        store.save_phase_results(
            cell_id=cell.cell_id,
            run_name="xss-phase-run",
            phases=[
                {
                    "name": "p1",
                    "trace_id": "tr-1",
                    "model": "m",
                    "model_role": "implementation",
                    "exit_code": 1,
                    "duration_ms": 100,
                    "timed_out": False,
                    "usage": None,
                    "total_cost": 0,
                    "num_api_calls": 1,
                    "error": xss_payload,
                },
            ],
        )

        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/run/xss-phase-run")
        assert resp.status_code == 200
        assert "<script>alert(99)</script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestDashboardEmpty:
    def test_home_with_no_data(self, tmp_path):
        """Test that the dashboard handles empty databases gracefully."""
        db_path = str(tmp_path / "empty.db")
        ResultsStore(db_path)  # Initialize empty store
        app = create_app(results_db=db_path)
        client = TestClient(app)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "No runs found" in resp.text

    def test_api_runs_empty(self, tmp_path):
        """Test that the API returns empty list when no runs exist."""
        db_path = str(tmp_path / "empty.db")
        ResultsStore(db_path)
        app = create_app(results_db=db_path)
        client = TestClient(app)

        resp = client.get("/api/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []


class TestDashboardTokenAuth:
    """Tests for optional bearer-token authentication."""

    TOKEN = "s3cret-token-abc123"

    @pytest.fixture
    def authed_client(self, store_with_results):
        """Dashboard with token auth enabled."""
        app = create_app(results_db=store_with_results, token=self.TOKEN)
        return TestClient(app)

    def test_no_token_returns_401(self, authed_client):
        """Requests without a token are rejected."""
        resp = authed_client.get("/api/runs")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    def test_wrong_token_returns_401(self, authed_client):
        """Requests with the wrong token are rejected."""
        resp = authed_client.get(
            "/api/runs", headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 401

    def test_valid_bearer_header(self, authed_client):
        """Valid token in Authorization header grants access."""
        resp = authed_client.get(
            "/api/runs", headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runs"]) == 1

    def test_valid_token_query_param(self, authed_client):
        """Valid token in ?token= query param grants access."""
        resp = authed_client.get(f"/api/runs?token={self.TOKEN}")
        assert resp.status_code == 200

    def test_html_request_returns_html_401(self, authed_client):
        """Browser requests get an HTML 401 page, not JSON."""
        resp = authed_client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 401
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Unauthorized" in resp.text

    def test_no_token_means_no_auth(self, store_with_results):
        """When token=None, all requests work as before (backward compat)."""
        app = create_app(results_db=store_with_results)
        client = TestClient(app)
        resp = client.get("/api/runs")
        assert resp.status_code == 200

    def test_token_protected_all_routes(self, authed_client):
        """Every route requires the token, not just /api/runs."""
        routes = [
            "/",
            "/run/test-run",
            "/api/runs",
            "/api/run/test-run",
            "/api/run/test-run/leaderboard",
            "/api/run/test-run/status",
            "/api/run/test-run/errors",
        ]
        for route in routes:
            resp = authed_client.get(route)
            assert resp.status_code == 401, f"Route {route} should require token"

    def test_empty_token_means_no_auth(self, store_with_results):
        """Empty string token is treated as None (no auth)."""
        app = create_app(results_db=store_with_results, token="")
        client = TestClient(app)
        resp = client.get("/api/runs")
        assert resp.status_code == 200

    def test_whitespace_token_means_no_auth(self, store_with_results):
        """Whitespace-only token is treated as None (no auth)."""
        app = create_app(results_db=store_with_results, token="   ")
        client = TestClient(app)
        resp = client.get("/api/runs")
        assert resp.status_code == 200

    def test_bearer_header_takes_precedence(self, authed_client):
        """Authorization header is checked before cookie/query param."""
        resp = authed_client.get(
            f"/api/runs?token={self.TOKEN}",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_token_not_in_response(self, authed_client):
        """The token must not leak into response bodies or headers."""
        resp = authed_client.get(
            "/api/runs", headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        assert self.TOKEN not in resp.text

    def test_unified_401_message(self, authed_client):
        """Missing and wrong tokens return the same error message (no info leak)."""
        resp_missing = authed_client.get("/api/runs")
        resp_wrong = authed_client.get(
            "/api/runs", headers={"Authorization": "Bearer wrong"}
        )
        # Both should have the same detail message
        assert resp_missing.json()["detail"] == resp_wrong.json()["detail"]

    def test_case_insensitive_bearer_prefix(self, authed_client):
        """Bearer prefix matching is case-insensitive."""
        for prefix in ("Bearer", "bearer", "BEARER", "BeArEr"):
            resp = authed_client.get(
                "/api/runs",
                headers={"Authorization": f"{prefix} {self.TOKEN}"},
            )
            assert resp.status_code == 200, f"Prefix '{prefix}' should work"

    def test_empty_bearer_token_rejected(self, authed_client):
        """'Authorization: Bearer ' (empty after prefix) is rejected."""
        resp = authed_client.get(
            "/api/runs", headers={"Authorization": "Bearer "}
        )
        assert resp.status_code == 401

    def test_docs_disabled_when_authed(self, store_with_results):
        """OpenAPI/docs endpoints are disabled when auth is on."""
        app = create_app(results_db=store_with_results, token=self.TOKEN)
        # The docs routes should not be registered in the app's router.
        route_paths = {route.path for route in app.routes}
        assert "/docs" not in route_paths
        assert "/redoc" not in route_paths
        assert "/openapi.json" not in route_paths

    def test_docs_available_when_no_auth(self, store_with_results):
        """OpenAPI/docs endpoints are available when no auth is configured."""
        app = create_app(results_db=store_with_results)
        client = TestClient(app)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200


class TestDashboardCookieAuth:
    """Tests for the cookie-based browser auth flow."""

    TOKEN = "s3cret-cookie-token"

    @pytest.fixture
    def authed_client(self, store_with_results):
        app = create_app(results_db=store_with_results, token=self.TOKEN)
        return TestClient(app)

    def test_login_with_valid_token_sets_cookie(self, authed_client):
        """GET /login?token=valid sets a cookie and redirects to /."""
        resp = authed_client.get(
            f"/login?token={self.TOKEN}", follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        set_cookie = resp.headers.get("set-cookie", "")
        assert "dashboard_token=" in set_cookie
        assert "HttpOnly" in set_cookie

    def test_login_with_wrong_token_shows_form(self, authed_client):
        """GET /login?token=wrong shows the login form (no cookie set)."""
        resp = authed_client.get("/login?token=wrong", follow_redirects=False)
        assert resp.status_code == 200
        assert "Login" in resp.text
        assert "dashboard_token" not in resp.headers.get("set-cookie", "")

    def test_login_without_token_shows_form(self, authed_client):
        """GET /login with no token shows the login form."""
        resp = authed_client.get("/login", follow_redirects=False)
        assert resp.status_code == 200
        assert "<form" in resp.text

    def test_cookie_grants_access(self, authed_client):
        """A valid dashboard_token cookie grants access to all routes."""
        # First, get the cookie via /login
        resp = authed_client.get(
            f"/login?token={self.TOKEN}", follow_redirects=False
        )
        cookie_value = resp.cookies.get("dashboard_token")
        assert cookie_value == self.TOKEN

        # Use the cookie for subsequent requests
        resp = authed_client.get(
            "/api/runs", cookies={"dashboard_token": cookie_value}
        )
        assert resp.status_code == 200

    def test_wrong_cookie_rejected(self, authed_client):
        """An invalid cookie value is rejected."""
        resp = authed_client.get(
            "/api/runs", cookies={"dashboard_token": "wrong-value"}
        )
        assert resp.status_code == 401

    def test_logout_clears_cookie(self, authed_client):
        """GET /logout clears the auth cookie."""
        resp = authed_client.get("/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"
        set_cookie = resp.headers.get("set-cookie", "")
        assert "dashboard_token=" in set_cookie
        # Cookie is being deleted (empty value or expired)
        assert '""' in set_cookie or "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()

    def test_login_redirects_when_no_auth(self, store_with_results):
        """When auth is disabled, /login redirects to /."""
        app = create_app(results_db=store_with_results)
        client = TestClient(app)
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_login_form_does_not_leak_token(self, authed_client):
        """The login form page must not contain the expected token."""
        resp = authed_client.get("/login")
        assert self.TOKEN not in resp.text


class TestDashboardCellDetail:
    """Tests for the per-cell detail page."""

    def test_cell_detail_returns_html(self, client, store_with_results):
        """Cell detail page renders for a valid cell."""
        # Get a cell_id from the API
        resp = client.get("/api/run/test-run?per_page=1")
        cell_id = resp.json()["results"][0]["cell_id"]
        resp = client.get(f"/run/test-run/cell/{cell_id}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert cell_id in resp.text

    def test_cell_detail_404_for_unknown_cell(self, client):
        """Cell detail returns 404 for a nonexistent cell."""
        resp = client.get("/run/test-run/cell/nonexistent-cell-id")
        assert resp.status_code == 404

    def test_cell_detail_404_for_unknown_run(self, client):
        """Cell detail returns 404 when the run doesn't exist."""
        resp = client.get("/run/nonexistent/cell/some-cell")
        assert resp.status_code == 404

    def test_cell_detail_shows_diff_and_test_output(self, tmp_path):
        """Cell detail page shows diff and test_output when present."""
        db_path = str(tmp_path / "cell_detail.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="test-run",
            harness=HarnessSpec(name="opencode", adapter="opencode"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t1", name="T1", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell,
            exit_class="pass",
            success=1.0,
            total_cost=0.01,
            latency_ms=5000,
            num_api_calls=2,
            diff="--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@",
            test_output="All tests passed",
            error_message="something went wrong",
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        cell_id = cell.cell_id
        resp = client.get(f"/run/test-run/cell/{cell_id}")
        assert resp.status_code == 200
        assert "Diff" in resp.text
        assert "Test Output" in resp.text
        assert "Error" in resp.text

    def test_cell_detail_xss_safe(self, tmp_path):
        """Cell detail escapes HTML in cell fields."""
        db_path = str(tmp_path / "cell_xss.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="test-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell,
            exit_class="pass",
            success=1.0,
            total_cost=0,
            latency_ms=100,
            num_api_calls=1,
            error_message='<script>alert("xss")</script>',
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get(f"/run/test-run/cell/{cell.cell_id}")
        assert resp.status_code == 200
        assert "<script>alert" not in resp.text

    def test_cell_detail_shows_harness_output(self, tmp_path):
        """Cell detail page shows harness stdout and stderr when present."""
        db_path = str(tmp_path / "harness_out.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="test-run",
            harness=HarnessSpec(name="claude-code", adapter="claude_code"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t1", name="T1", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell,
            exit_class="fail",
            success=0.0,
            error_class="no_change",
            error_message="No changes were made to the repository",
            harness_stdout="Starting claude-code...\nProcessing task...",
            harness_stderr="Error: API authentication failed",
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get(f"/run/test-run/cell/{cell.cell_id}")
        assert resp.status_code == 200
        assert "Harness Output" in resp.text
        assert "stderr" in resp.text
        assert "stdout" in resp.text
        assert "Error: API authentication failed" in resp.text
        assert "Starting claude-code..." in resp.text

    def test_cell_detail_shows_no_output_message(self, tmp_path):
        """Cell detail shows 'No harness output' when stdout/stderr are empty."""
        db_path = str(tmp_path / "no_output.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="test-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell,
            exit_class="pass",
            success=1.0,
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get(f"/run/test-run/cell/{cell.cell_id}")
        assert resp.status_code == 200
        assert "No harness output was captured" in resp.text

    def test_cell_detail_harness_output_xss_safe(self, tmp_path):
        """Harness stdout/stderr with HTML payloads must be escaped."""
        db_path = str(tmp_path / "harness_xss.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="test-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        xss = '<script>alert("xss")</script>'
        store.save_result(
            cell=cell,
            exit_class="fail",
            success=0.0,
            harness_stdout=xss,
            harness_stderr=xss,
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get(f"/run/test-run/cell/{cell.cell_id}")
        assert resp.status_code == 200
        assert "<script>alert" not in resp.text

    def test_cell_detail_shows_phase_output(self, tmp_path):
        """Cell detail page shows per-phase stdout/stderr in collapsible details."""
        db_path = str(tmp_path / "phase_out.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="mp-run",
            harness=HarnessSpec(name="claude-code", adapter="claude_code"),
            model=ModelSpec(name="sonnet", provider="anthropic", api_key_env="X"),
            task=TaskSpec(
                id="mp-task",
                name="MP",
                track=TaskTrack.MULTI_PHASE,
                task_prompt="p",
                phases=[PhaseSpec(name="implement", task_prompt="p")],
            ),
            repeat=0,
        )
        store.save_result(
            cell=cell,
            exit_class="fail",
            success=0.0,
        )
        store.save_phase_results(
            cell.cell_id,
            "mp-run",
            [
                {
                    "name": "implement",
                    "trace_id": f"{cell.cell_id}__phase-implement",
                    "model": "sonnet",
                    "model_role": "implementation",
                    "exit_code": 0,
                    "duration_ms": 5000.0,
                    "timed_out": False,
                    "stdout": "Implementing fix...",
                    "stderr": "Warning: deprecated",
                },
            ],
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get(f"/run/mp-run/cell/{cell.cell_id}")
        assert resp.status_code == 200
        assert "implement" in resp.text
        assert "Implementing fix..." in resp.text
        assert "Warning: deprecated" in resp.text

    def test_csv_export_includes_harness_output(self, tmp_path):
        """CSV export includes harness_stdout and harness_stderr columns."""
        db_path = str(tmp_path / "export_out.db")
        store = ResultsStore(db_path)
        cell = RunCell(
            run_name="test-run",
            harness=HarnessSpec(name="h", adapter="h"),
            model=ModelSpec(name="m", provider="anthropic", api_key_env="X"),
            task=TaskSpec(id="t", name="T", track=TaskTrack.SWE, task_prompt="p"),
            repeat=0,
        )
        store.save_result(
            cell=cell,
            exit_class="fail",
            success=0.0,
            harness_stdout="some stdout",
            harness_stderr="some stderr",
        )
        app = create_app(results_db=db_path)
        client = TestClient(app)
        resp = client.get("/run/test-run/export/csv")
        assert resp.status_code == 200
        assert "harness_stdout" in resp.text
        assert "harness_stderr" in resp.text
        assert "some stdout" in resp.text
        assert "some stderr" in resp.text


class TestDashboardSort:
    """Tests for server-side sortable columns."""

    def test_sort_by_success(self, client):
        """Sort results by success descending."""
        resp = client.get("/run/test-run", params={"sort": "success", "order": "desc"})
        assert resp.status_code == 200
        # The sort indicator should be present
        assert "↓" in resp.text or "sort-indicator" in resp.text

    def test_sort_by_cost(self, client):
        """Sort results by total_cost ascending."""
        resp = client.get("/run/test-run", params={"sort": "total_cost", "order": "asc"})
        assert resp.status_code == 200
        assert "↑" in resp.text or "sort-indicator" in resp.text

    def test_sort_invalid_column_ignored(self, client):
        """Invalid sort column falls back to default ordering."""
        resp = client.get("/run/test-run", params={"sort": "DROP TABLE", "order": "asc"})
        assert resp.status_code == 200
        # Should still show results
        assert "opencode" in resp.text

    def test_sort_aria_sort_present(self, client):
        """Sortable headers have aria-sort attribute."""
        resp = client.get("/run/test-run", params={"sort": "success", "order": "desc"})
        assert 'aria-sort="descending"' in resp.text

    def test_api_sort_supported(self, client):
        """API endpoint supports sort parameter."""
        resp = client.get("/api/run/test-run", params={"sort": "success", "order": "desc"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) > 0


class TestDashboardExport:
    """Tests for CSV/JSON export."""

    def test_export_csv(self, client):
        """CSV export returns text/csv with content."""
        resp = client.get("/run/test-run/export/csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "attachment" in resp.headers.get("content-disposition", "")
        # Should contain header row
        assert "cell_id" in resp.text
        assert "harness" in resp.text

    def test_export_json(self, client):
        """JSON export returns application/json with content."""
        resp = client.get("/run/test-run/export/json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")
        data = resp.json()
        assert data["run_name"] == "test-run"
        assert len(data["results"]) > 0

    def test_export_csv_with_filter(self, client):
        """CSV export respects filters."""
        resp = client.get("/run/test-run/export/csv", params={"harness": "opencode"})
        assert resp.status_code == 200
        # Should only contain opencode rows (4 results)
        lines = resp.text.strip().split("\n")
        # 1 header + 4 data rows
        assert len(lines) == 5

    def test_export_invalid_format(self, client):
        """Invalid format returns 400."""
        resp = client.get("/run/test-run/export/xml")
        assert resp.status_code == 400

    def test_export_404_for_unknown_run(self, client):
        """Export returns 404 for unknown run."""
        resp = client.get("/run/nonexistent/export/csv")
        assert resp.status_code == 404


class TestDashboardTheme:
    """Tests for theme cookie support."""

    def test_theme_cookie_passed_to_template(self, client):
        """Theme cookie value appears in data-theme attribute."""
        resp = client.get("/", cookies={"theme": "dark"})
        assert resp.status_code == 200
        assert 'data-theme="dark"' in resp.text

    def test_no_theme_cookie_defaults_to_auto(self, client):
        """Without a theme cookie, data-theme is 'auto'."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'data-theme="auto"' in resp.text

    def test_theme_cookie_on_run_detail(self, client):
        """Theme cookie works on run detail page too."""
        resp = client.get("/run/test-run", cookies={"theme": "light"})
        assert resp.status_code == 200
        assert 'data-theme="light"' in resp.text


class TestDashboardAccessibility:
    """Tests for accessibility improvements."""

    def test_home_has_viewport_meta(self, client):
        """Home page has viewport meta tag."""
        resp = client.get("/")
        assert 'name="viewport"' in resp.text

    def test_home_has_lang_attr(self, client):
        """Home page has html lang attribute."""
        resp = client.get("/")
        assert '<html lang="en"' in resp.text

    def test_home_has_skip_link(self, client):
        """Home page has a skip link."""
        resp = client.get("/")
        assert "skip-link" in resp.text
        assert 'href="#main"' in resp.text

    def test_run_detail_has_filter_labels(self, client):
        """Filter form has visible labels."""
        resp = client.get("/run/test-run")
        assert "<label" in resp.text
        assert 'for="filter-model"' in resp.text
        assert 'for="filter-harness"' in resp.text

    def test_run_detail_tables_have_thead(self, client):
        """Tables have proper thead/tbody structure."""
        resp = client.get("/run/test-run")
        assert "<thead>" in resp.text
        assert "<tbody>" in resp.text
        assert 'scope="col"' in resp.text

    def test_run_detail_has_nav_landmark(self, client):
        """Run detail page has nav landmark."""
        resp = client.get("/run/test-run")
        assert "<nav" in resp.text

    def test_pagination_uses_ellipsis_char(self, client):
        """Pagination uses … (ellipsis) not ... (three dots)."""
        resp = client.get("/run/test-run", params={"per_page": 2, "page": 1})
        if "…" not in resp.text and "..." in resp.text:
            pytest.fail("Pagination uses '...' instead of '…'")

    def test_home_has_copy_button(self, client):
        """Run cards have copy-to-clipboard buttons."""
        resp = client.get("/")
        assert "copy-btn" in resp.text
        assert "data-copy" in resp.text

    def test_run_detail_has_export_links(self, client):
        """Run detail page has CSV and JSON export links."""
        resp = client.get("/run/test-run")
        assert "Export CSV" in resp.text
        assert "Export JSON" in resp.text

    def test_run_detail_has_cell_links(self, client):
        """Results table links to cell detail pages."""
        resp = client.get("/run/test-run")
        assert "/cell/" in resp.text
