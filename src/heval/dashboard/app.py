"""FastAPI dashboard for interactive exploration of eval results.

Features:
  - Live progress tracking for running evals (via run_state)
  - Leaderboard views (within-model and cross-model)
  - Filtering by model, harness, task track, observability tier, reliability
  - Detailed per-cell results with pagination
  - Cost analysis views
  - REST API with consistent JSON responses

Security: All HTML is rendered through Jinja2 with autoescaping enabled.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from heval.orchestrator.results_store import ResultsStore
from heval.reporting.static_report import ReportGenerator

# Jinja2 environment with autoescaping for XSS safety
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# SQL for aggregate queries (avoids loading full table)
SQL_RUN_SUMMARIES = """
SELECT run_name,
       COUNT(*) as total_cells,
       SUM(CASE WHEN exit_class = 'pass' THEN 1 ELSE 0 END) as passed,
       SUM(CASE WHEN exit_class != 'pass' THEN 1 ELSE 0 END) as failed,
       COALESCE(SUM(total_cost), 0) as total_cost,
       COALESCE(AVG(success), 0) as avg_success
FROM run_results
GROUP BY run_name
ORDER BY run_name
"""

SQL_RUN_NAMES = """
SELECT DISTINCT run_name FROM run_results
UNION
SELECT DISTINCT run_name FROM run_state
ORDER BY run_name
"""

SQL_RUN_STATE_SUMMARY = """
SELECT status, COUNT(*) as count
FROM run_state
WHERE run_name = ?
GROUP BY status
"""

SQL_PAGINATED_RESULTS = """
SELECT * FROM run_results
WHERE run_name = ?
  AND (? IS NULL OR model = ?)
  AND (? IS NULL OR harness = ?)
  AND (? IS NULL OR track = ?)
  AND (? IS NULL OR success >= ?)
ORDER BY harness, model, task_id, repeat
LIMIT ? OFFSET ?
"""

SQL_FILTERED_COUNT = """
SELECT COUNT(*) FROM run_results
WHERE run_name = ?
  AND (? IS NULL OR model = ?)
  AND (? IS NULL OR harness = ?)
  AND (? IS NULL OR track = ?)
  AND (? IS NULL OR success >= ?)
"""

SQL_UNIQUE_VALUES = """
SELECT DISTINCT {column} FROM run_results
WHERE run_name = ?
ORDER BY {column}
"""


def create_app(results_db: str = "heval_results.db") -> FastAPI:
    """Create the FastAPI dashboard app.

    Args:
        results_db: Path to the results SQLite database.
    """
    app = FastAPI(
        title="heval Dashboard",
        description="Interactive dashboard for harness evaluation results",
        version="0.1.0",
    )
    store = ResultsStore(results_db)
    report_gen = ReportGenerator(store)
    db_path = Path(results_db)

    def _get_db_conn() -> contextlib.closing[sqlite3.Connection]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return contextlib.closing(conn)

    def _get_run_summaries() -> list[dict[str, Any]]:
        """Get aggregate summaries for all runs (SQL-level, no full table load)."""
        with _get_db_conn() as conn:
            rows = conn.execute(SQL_RUN_SUMMARIES).fetchall()
            return [dict(r) for r in rows]

    def _run_exists(run_name: str) -> bool:
        """Check if a run exists (in either run_results or run_state)."""
        with _get_db_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM run_results WHERE run_name = ? LIMIT 1",
                (run_name,),
            ).fetchone()
            if row:
                return True
            row = conn.execute(
                "SELECT 1 FROM run_state WHERE run_name = ? LIMIT 1",
                (run_name,),
            ).fetchone()
            return row is not None

    def _get_run_state_summary(run_name: str) -> dict[str, int]:
        """Get live progress from run_state table."""
        with _get_db_conn() as conn:
            rows = conn.execute(SQL_RUN_STATE_SUMMARY, (run_name,)).fetchall()
            return {row["status"]: row["count"] for row in rows}

    def _get_unique_values(run_name: str, column: str) -> list[str]:
        """Get unique values for a column (for filter dropdowns)."""
        # Validate column name to prevent SQL injection
        allowed = {"model", "harness", "track"}
        if column not in allowed:
            return []
        with _get_db_conn() as conn:
            rows = conn.execute(
                SQL_UNIQUE_VALUES.format(column=column), (run_name,)
            ).fetchall()
            return [r[0] for r in rows if r[0]]

    def _get_paginated_results(
        run_name: str,
        model: str | None,
        harness: str | None,
        track: str | None,
        min_success: float | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Get paginated, filtered results."""
        with _get_db_conn() as conn:
            rows = conn.execute(
                SQL_PAGINATED_RESULTS,
                (
                    run_name,
                    model, model,
                    harness, harness,
                    track, track,
                    min_success, min_success,
                    limit, offset,
                ),
            ).fetchall()
            return [dict(r) for r in rows]

    def _get_filtered_count(
        run_name: str,
        model: str | None,
        harness: str | None,
        track: str | None,
        min_success: float | None,
    ) -> int:
        """Get count of filtered results (without loading them all)."""
        with _get_db_conn() as conn:
            row = conn.execute(
                SQL_FILTERED_COUNT,
                (
                    run_name,
                    model, model,
                    harness, harness,
                    track, track,
                    min_success, min_success,
                ),
            ).fetchone()
            return row[0] if row else 0

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Dashboard home page with run overview."""
        runs = await asyncio.to_thread(_get_run_summaries)
        template = _env.get_template("index.html")
        return template.render(runs=runs)

    def _get_run_summary_stats(run_name: str) -> tuple[int, int, float]:
        """Get total/passed/cost for a run in a single query."""
        with _get_db_conn() as conn:
            summary_row = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN exit_class = 'pass' THEN 1 ELSE 0 END) as passed,
                          COALESCE(SUM(total_cost), 0) as cost
                   FROM run_results WHERE run_name = ?""",
                (run_name,),
            ).fetchone()
            return (
                summary_row[0] or 0,
                summary_row[1] or 0,
                summary_row[2] or 0.0,
            )

    @app.get("/run/{run_name}", response_class=HTMLResponse)
    async def run_detail(
        run_name: str,
        model: str | None = Query(None),
        harness: str | None = Query(None),
        track: str | None = Query(None),
        min_success: float | None = Query(None, ge=0.0, le=1.0),
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=500),
    ) -> str:
        """Detailed view for a specific run with filtering and pagination."""
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")

        # Get filter options
        models = await asyncio.to_thread(_get_unique_values, run_name, "model")
        harnesses = await asyncio.to_thread(_get_unique_values, run_name, "harness")
        tracks = await asyncio.to_thread(_get_unique_values, run_name, "track")

        # Clamp the requested page to the available range so an out-of-range
        # page returns the last page rather than an empty, misleading result.
        total_count = await asyncio.to_thread(
            _get_filtered_count, run_name, model, harness, track, min_success
        )
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        results = await asyncio.to_thread(
            _get_paginated_results,
            run_name, model, harness, track, min_success, per_page, offset,
        )

        # Get live state (if run is in progress)
        state_summary = await asyncio.to_thread(_get_run_state_summary, run_name)

        # Build leaderboard from full run (not filtered)
        all_results = await asyncio.to_thread(store.get_all_results, run_name)
        leaderboards = await asyncio.to_thread(
            report_gen._build_leaderboards, all_results
        )

        # Summary stats from SQL aggregation
        total_cells, passed, cost = await asyncio.to_thread(
            _get_run_summary_stats, run_name
        )

        template = _env.get_template("run_detail.html")
        return template.render(
            run_name=run_name,
            results=results,
            leaderboards=leaderboards,
            models=models,
            harnesses=harnesses,
            tracks=tracks,
            current_model=model,
            current_harness=harness,
            current_track=track,
            current_min_success=min_success,
            page=page,
            per_page=per_page,
            total_count=total_count,
            total_pages=total_pages,
            total_cells=total_cells,
            passed=passed,
            cost=cost,
            state_summary=state_summary,
        )

    @app.get("/api/runs")
    async def api_list_runs() -> dict[str, Any]:
        """List all runs with summary stats (SQL aggregation)."""
        runs = await asyncio.to_thread(_get_run_summaries)
        return {"runs": runs}

    @app.get("/api/run/{run_name}")
    async def api_run_results(
        run_name: str,
        model: str | None = Query(None),
        harness: str | None = Query(None),
        track: str | None = Query(None),
        min_success: float | None = Query(None, ge=0.0, le=1.0),
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=500),
    ) -> JSONResponse:
        """Get filtered, paginated results for a run as JSON."""
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")

        total_count = await asyncio.to_thread(
            _get_filtered_count, run_name, model, harness, track, min_success
        )
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        # Clamp to the last page so out-of-range requests don't report a page
        # number with an empty result set.
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        results = await asyncio.to_thread(
            _get_paginated_results,
            run_name, model, harness, track, min_success, per_page, offset,
        )

        return JSONResponse(
            {
                "run_name": run_name,
                "page": page,
                "per_page": per_page,
                "total": total_count,
                "total_pages": total_pages,
                "count": len(results),
                "results": results,
            }
        )

    @app.get("/api/run/{run_name}/leaderboard")
    async def api_leaderboard(run_name: str) -> dict[str, Any]:
        """Get leaderboard data for a run."""
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")
        results = await asyncio.to_thread(store.get_all_results, run_name)
        leaderboards = await asyncio.to_thread(
            report_gen._build_leaderboards, results
        )
        return {"run_name": run_name, "leaderboards": leaderboards}

    @app.get("/api/run/{run_name}/status")
    async def api_run_status(run_name: str) -> dict[str, Any]:
        """Get live progress for a run (from run_state table)."""
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")
        state = await asyncio.to_thread(_get_run_state_summary, run_name)
        return {"run_name": run_name, "state": state}

    return app
