"""Tests for the FastAPI dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from heval.dashboard.app import create_app
from heval.orchestrator.config import HarnessSpec, ModelSpec, RunCell, TaskSpec, TaskTrack
from heval.orchestrator.results_store import ResultsStore


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
        assert "harnessbench Dashboard" in resp.text

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
