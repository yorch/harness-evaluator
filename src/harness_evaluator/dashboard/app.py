"""FastAPI dashboard for interactive exploration of eval results.

Features:
  - Live progress tracking for running evals (via run_state)
  - Leaderboard views (within-model and cross-model)
  - Filtering by model, harness, task track, observability tier, reliability
  - Detailed per-cell results with pagination
  - Cost analysis views
  - REST API with consistent JSON responses
  - Optional bearer-token authentication (--token)

Security: All HTML is rendered through Jinja2 with autoescaping enabled.
When a token is configured, every request must include it via one of:
  - ``Authorization: Bearer <token>`` header (preferred for API clients)
  - ``?token=<token>`` query parameter (sets an HttpOnly cookie via /login)
  - ``dashboard_token`` HttpOnly cookie (set by the /login endpoint)

The recommended browser flow is to visit ``/login?token=<secret>``, which
validates the token, sets an ``HttpOnly`` cookie, and redirects to ``/``.
This avoids leaving the token in the URL for every subsequent request
(browser history, Referer headers, server access logs). API clients should
use the ``Authorization`` header instead.

Token comparison uses ``hmac.compare_digest`` on SHA-256 hashes of both
values to prevent timing attacks and avoid leaking the token length.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import hmac
import io
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request as StarletteRequest

from harness_evaluator.orchestrator.results_store import ResultsStore
from harness_evaluator.reporting.static_report import ReportGenerator, sanitize_csv_field

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
ORDER BY {order_by}
LIMIT ? OFFSET ?
"""

SQL_ALL_FILTERED = """
SELECT * FROM run_results
WHERE run_name = ?
  AND (? IS NULL OR model = ?)
  AND (? IS NULL OR harness = ?)
  AND (? IS NULL OR track = ?)
  AND (? IS NULL OR success >= ?)
ORDER BY {order_by}
"""

SORT_COLUMNS = frozenset({
    "harness", "model", "task_id", "repeat", "exit_class",
    "success", "total_cost", "latency_ms", "num_api_calls",
})

DEFAULT_ORDER_BY = "harness, model, task_id, repeat"


def _build_order_by(sort: str | None, order: str | None) -> str:
    """Build a safe ORDER BY clause from validated sort column and direction."""
    if sort and sort in SORT_COLUMNS:
        direction = "DESC" if (order or "").upper() == "DESC" else "ASC"
        return f"{sort} {direction}"
    return DEFAULT_ORDER_BY

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


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not carry the expected bearer token.

    The token may be supplied via (checked in order):
      1. ``Authorization: Bearer <token>`` header (preferred for API clients)
      2. ``dashboard_token`` HttpOnly cookie (set by the /login endpoint)
      3. ``?token=<token>`` query parameter (redirects to /login to set cookie)

    Token comparison hashes both values with SHA-256 before
    ``hmac.compare_digest`` to prevent timing attacks and avoid leaking
    the expected token length. When ``expected_token`` is ``None``, the
    middleware is a no-op.
    """

    COOKIE_NAME = "dashboard_token"
    # Paths that bypass auth: /login (entry point) and /logout (cookie clear).
    PUBLIC_PATHS = frozenset({"/login", "/logout"})

    def __init__(self, app: Any, expected_token: str | None) -> None:
        super().__init__(app)
        self._expected_token = expected_token

    async def dispatch(
        self, request: StarletteRequest, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._expected_token is None:
            return await call_next(request)

        # /login and /logout must be reachable without a token.
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        provided = self._extract_token(request)
        if not provided:
            return self._unauthorized(request)

        if not self._tokens_match(provided, self._expected_token):
            return self._unauthorized(request)

        return await call_next(request)

    @staticmethod
    def _tokens_match(provided: str, expected: str) -> bool:
        """Constant-time comparison that does not leak token length.

        Both values are SHA-256 hashed before ``hmac.compare_digest`` so
        the comparison is always between 64-byte hex digests, eliminating
        the length-based timing leak present in raw ``compare_digest``.
        """
        provided_hash = hashlib.sha256(provided.encode()).hexdigest()
        expected_hash = hashlib.sha256(expected.encode()).hexdigest()
        return hmac.compare_digest(provided_hash, expected_hash)

    @staticmethod
    def _extract_token(request: StarletteRequest) -> str | None:
        """Extract the token from header, cookie, or query string."""
        # 1. Authorization header (preferred for API clients)
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            return token or None
        # 2. HttpOnly cookie (set by /login endpoint for browser sessions)
        cookie_token = request.cookies.get(TokenAuthMiddleware.COOKIE_NAME)
        if cookie_token:
            return cookie_token
        # 3. ?token= query param (for initial login / API convenience)
        return request.query_params.get("token")

    @staticmethod
    def _unauthorized(request: StarletteRequest) -> Response:
        """Return a 401 response with a unified message.

        The same message is used for missing and invalid tokens to avoid
        revealing whether a guess was considered (information disclosure).
        For HTML requests (browser), return a simple HTML page with a
        link to /login. For API requests, return JSON.
        """
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                status_code=401,
                content=(
                    "<!DOCTYPE html><html lang='en'><head>"
                    "<meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                    "<title>401 Unauthorized</title>"
                    "<style>a{color:#1971c2}</style>"
                    "</head><body><h1>401 Unauthorized</h1>"
                    "<p>Authentication required. Use the login link below.</p>"
                    '<p><a href="/login">Login</a></p>'
                    "</body></html>"
                ),
                headers={"WWW-Authenticate": 'Bearer realm="harness-evaluator"'},
            )
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": 'Bearer realm="harness-evaluator"'},
        )


def create_app(
    results_db: str = "harness_evaluator_results.db",
    token: str | None = None,
) -> FastAPI:
    """Create the FastAPI dashboard app.

    Args:
        results_db: Path to the results SQLite database.
        token: If set, all requests must include this token via the
            ``Authorization: Bearer <token>`` header, the
            ``dashboard_token`` cookie (set by ``/login``), or the
            ``?token=<token>`` query parameter. If ``None`` or empty
            (default), no auth is required.
    """
    # Normalize: strip whitespace; empty string means "no auth".
    auth_token = (token or "").strip() or None

    app = FastAPI(
        title="harness-evaluator Dashboard",
        description="Interactive dashboard for harness evaluation results",
        version="0.1.0",
        # Disable auto-docs endpoints when auth is on to reduce info leak.
        docs_url="/docs" if auth_token is None else None,
        redoc_url="/redoc" if auth_token is None else None,
        openapi_url="/openapi.json" if auth_token is None else None,
    )

    if auth_token is not None:
        app.add_middleware(TokenAuthMiddleware, expected_token=auth_token)

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
        sort: str | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get paginated, filtered results."""
        order_by = _build_order_by(sort, order)
        sql = SQL_PAGINATED_RESULTS.format(order_by=order_by)
        with _get_db_conn() as conn:
            rows = conn.execute(
                sql,
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

    def _get_all_filtered_results(
        run_name: str,
        model: str | None,
        harness: str | None,
        track: str | None,
        min_success: float | None,
        sort: str | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all filtered results (no pagination, for export)."""
        order_by = _build_order_by(sort, order)
        sql = SQL_ALL_FILTERED.format(order_by=order_by)
        with _get_db_conn() as conn:
            rows = conn.execute(
                sql,
                (
                    run_name,
                    model, model,
                    harness, harness,
                    track, track,
                    min_success, min_success,
                ),
            ).fetchall()
            return [dict(r) for r in rows]

    def _get_theme(request: StarletteRequest) -> str | None:
        """Read the theme cookie ('light', 'dark', or None for auto)."""
        return request.cookies.get("theme")

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

    def _get_failed_cells(run_name: str) -> list[dict[str, Any]]:
        with _get_db_conn() as conn:
            state_rows = conn.execute(
                "SELECT cell_id, status, error FROM run_state"
                " WHERE run_name = ? AND status IN ('failed', 'skipped')"
                " ORDER BY status, cell_id",
                (run_name,),
            ).fetchall()
            result_rows = conn.execute(
                "SELECT cell_id, exit_class, error_class, error_message"
                " FROM run_results"
                " WHERE run_name = ? AND exit_class != 'pass'"
                " ORDER BY cell_id",
                (run_name,),
            ).fetchall()
        seen: set[str] = set()
        cells: list[dict[str, Any]] = []
        for row in state_rows:
            cells.append(
                {"cell_id": row["cell_id"], "status": row["status"], "error": row["error"] or ""}
            )
            seen.add(row["cell_id"])
        for row in result_rows:
            if row["cell_id"] in seen:
                continue
            msg = row["error_message"] or ""
            if row["error_class"]:
                msg = f"{row['error_class']}: {msg}" if msg else row["error_class"]
            cells.append({"cell_id": row["cell_id"], "status": "failed", "error": msg})
        return cells

    def _get_phase_results_for_cells(
        cell_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if not cell_ids:
            return {}
        placeholders = ",".join("?" * len(cell_ids))
        with _get_db_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM phase_results WHERE cell_id IN ({placeholders})"
                " ORDER BY cell_id, id ASC",
                cell_ids,
            ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(row["cell_id"], []).append(dict(row))
        return result

    # --- Login endpoint (only meaningful when auth is enabled) ---

    @app.get("/login", response_class=HTMLResponse)
    async def login(
        token: str | None = Query(None, description="Bearer token to set as cookie"),
    ) -> Response:
        """Validate the token and set an HttpOnly cookie, then redirect to /.

        This is the recommended browser entry point when auth is enabled:
        visit ``/login?token=<secret>`` once. If the token is valid, a
        ``dashboard_token`` HttpOnly cookie is set and the browser is
        redirected to ``/``. Subsequent requests carry the cookie
        automatically, so the token does not remain in the URL.

        If no token is provided or it is invalid, a login form is shown.
        If auth is not enabled, this endpoint redirects to ``/``.
        """
        if auth_token is None:
            return RedirectResponse(url="/", status_code=302)

        if token and TokenAuthMiddleware._tokens_match(token, auth_token):
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie(
                key=TokenAuthMiddleware.COOKIE_NAME,
                value=token,
                httponly=True,
                samesite="lax",
                secure=False,  # dashboard is typically HTTP on LAN
                max_age=86400,  # 24 hours
            )
            return resp

        # Show a simple login form (token is missing or invalid)
        return HTMLResponse(
            content=(
                "<!DOCTYPE html><html lang='en'><head>"
                "<meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Login</title>"
                "<style>body{font-family:-apple-system,sans-serif;margin:2rem}"
                "label{display:block;margin:1rem 0}"
                "input{padding:0.35rem 0.5rem;font-size:1rem}"
                "button{padding:0.35rem 1rem;cursor:pointer}</style>"
                "</head><body><h1>Dashboard Login</h1>"
                "<form method='get' action='/login'>"
                "<label>Token: <input type='password' name='token' "
                "autocomplete='off'></label>"
                " <button type='submit'>Login</button>"
                "</form></body></html>"
            )
        )

    @app.get("/logout")
    async def logout() -> Response:
        """Clear the auth cookie and redirect to /login."""
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie(TokenAuthMiddleware.COOKIE_NAME)
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index(request: StarletteRequest) -> str:
        """Dashboard home page with run overview."""
        runs = await asyncio.to_thread(_get_run_summaries)
        theme = _get_theme(request)
        template = _env.get_template("index.html")
        return template.render(runs=runs, theme=theme)

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
        request: StarletteRequest,
        run_name: str,
        model: str | None = Query(None),
        harness: str | None = Query(None),
        track: str | None = Query(None),
        min_success: float | None = Query(None, ge=0.0, le=1.0),
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=500),
        sort: str | None = Query(None),
        order: str | None = Query(None),
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
            run_name, model, harness, track, min_success,
            per_page, offset, sort, order,
        )

        cell_ids = [r["cell_id"] for r in results]
        phase_results = await asyncio.to_thread(_get_phase_results_for_cells, cell_ids)
        failed_cells = await asyncio.to_thread(_get_failed_cells, run_name)

        # Get live state (if run is in progress)
        state_summary = await asyncio.to_thread(_get_run_state_summary, run_name)
        is_in_progress = bool(state_summary) and (
            "running" in state_summary or "pending" in state_summary
        )

        # Build leaderboard from full run (not filtered)
        all_results = await asyncio.to_thread(store.get_all_results, run_name)
        leaderboards = await asyncio.to_thread(
            report_gen._build_leaderboards, all_results
        )

        # Summary stats from SQL aggregation
        total_cells, passed, cost = await asyncio.to_thread(
            _get_run_summary_stats, run_name
        )

        # Run metadata for reproducibility panel
        run_metadata = await asyncio.to_thread(store.get_run_metadata, run_name)

        # Build base query string for pagination/sort links (without page/sort/order)
        base_query = urlencode({
            "model": model or "",
            "harness": harness or "",
            "track": track or "",
            "min_success": min_success if min_success is not None else "",
            "per_page": per_page,
        })

        theme = _get_theme(request)
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
            failed_cells=failed_cells,
            phase_results=phase_results,
            is_in_progress=is_in_progress,
            run_metadata=run_metadata,
            sort=sort,
            order=order,
            base_query=base_query,
            theme=theme,
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
        sort: str | None = Query(None),
        order: str | None = Query(None),
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
            run_name, model, harness, track, min_success,
            per_page, offset, sort, order,
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

    @app.get("/api/run/{run_name}/errors")
    async def api_run_errors(run_name: str) -> dict[str, Any]:
        """Get failed/skipped cells with error messages for a run."""
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")
        failed_cells = await asyncio.to_thread(_get_failed_cells, run_name)
        return {"run_name": run_name, "failed_cells": failed_cells}

    @app.get(
        "/run/{run_name}/cell/{cell_id}", response_class=HTMLResponse
    )
    async def cell_detail(
        request: StarletteRequest,
        run_name: str,
        cell_id: str,
    ) -> str:
        """Detailed view for a single evaluation cell."""
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")

        cell = await asyncio.to_thread(store.get_result, cell_id)
        if not cell or cell.get("run_name") != run_name:
            raise HTTPException(
                status_code=404,
                detail=f"Cell '{cell_id}' not found in run '{run_name}'",
            )

        phases = await asyncio.to_thread(store.get_phase_results, cell_id)
        reconciliation = await asyncio.to_thread(
            store.get_reconciliation_result, cell_id
        )
        theme = _get_theme(request)
        template = _env.get_template("cell_detail.html")
        return template.render(
            run_name=run_name,
            cell=cell,
            phases=phases,
            reconciliation=reconciliation,
            theme=theme,
        )

    @app.get("/run/{run_name}/export/{format}")
    async def export_results(
        run_name: str,
        format: str,
        model: str | None = Query(None),
        harness: str | None = Query(None),
        track: str | None = Query(None),
        min_success: float | None = Query(None, ge=0.0, le=1.0),
        sort: str | None = Query(None),
        order: str | None = Query(None),
    ) -> Response:
        """Export filtered results as CSV or JSON."""
        if format not in ("csv", "json"):
            raise HTTPException(
                status_code=400, detail="Format must be 'csv' or 'json'"
            )
        if not await asyncio.to_thread(_run_exists, run_name):
            raise HTTPException(status_code=404, detail=f"Run '{run_name}' not found")

        results = await asyncio.to_thread(
            _get_all_filtered_results,
            run_name, model, harness, track, min_success, sort, order,
        )

        if format == "json":
            return Response(
                content=json.dumps(
                    {"run_name": run_name, "count": len(results), "results": results},
                    indent=2,
                ),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="{run_name}.json"'
                },
            )

        # CSV export with CSV-injection sanitization
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "cell_id", "run_name", "harness", "model", "task_id", "track",
                "repeat", "exit_class", "success", "error_class", "error_message",
                "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "total_cost",
                "latency_ms", "time_to_first_attempt_ms", "num_api_calls",
                "num_tool_calls", "diff", "test_output", "harness_metadata",
                "timestamp", "retry_count",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in results:
            writer.writerow({k: sanitize_csv_field(v) for k, v in row.items()})

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{run_name}.csv"'
            },
        )

    return app
