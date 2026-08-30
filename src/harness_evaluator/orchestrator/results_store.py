"""Results store: per-cell metrics and run state.

Uses SQLite for structured storage of run results.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness_evaluator.gateway.models import TokenUsage
from harness_evaluator.orchestrator.config import RunCell

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_results (
    cell_id TEXT PRIMARY KEY,
    run_name TEXT NOT NULL,
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    task_id TEXT NOT NULL,
    track TEXT NOT NULL,
    repeat INTEGER NOT NULL,
    exit_class TEXT NOT NULL,
    success REAL NOT NULL,
    error_class TEXT,
    error_message TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    latency_ms REAL DEFAULT 0.0,
    time_to_first_attempt_ms REAL DEFAULT 0.0,
    num_api_calls INTEGER DEFAULT 0,
    num_tool_calls INTEGER DEFAULT 0,
    diff TEXT,
    test_output TEXT,
    harness_metadata TEXT,
    timestamp TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_run_name ON run_results(run_name);
CREATE INDEX IF NOT EXISTS idx_harness_model ON run_results(harness, model);
CREATE INDEX IF NOT EXISTS idx_task ON run_results(task_id);

CREATE TABLE IF NOT EXISTS run_state (
    cell_id TEXT PRIMARY KEY,
    run_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_state_name ON run_state(run_name);

CREATE TABLE IF NOT EXISTS run_metadata (
    run_name TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    harness_evaluator_version TEXT,
    docker_image TEXT,
    created_at TEXT NOT NULL
);
"""


class ResultsStore:
    """SQLite-backed store for evaluation results."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def save_result(
        self,
        cell: RunCell,
        exit_class: str,
        success: float,
        error_class: str | None = None,
        error_message: str | None = None,
        usage: TokenUsage | None = None,
        total_cost: float = 0.0,
        latency_ms: float = 0.0,
        time_to_first_attempt_ms: float = 0.0,
        num_api_calls: int = 0,
        num_tool_calls: int = 0,
        diff: str | None = None,
        test_output: str | None = None,
        harness_metadata: dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> None:
        usage = usage or TokenUsage()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success, error_class, error_message,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, total_cost, latency_ms, time_to_first_attempt_ms,
                    num_api_calls, num_tool_calls, diff, test_output,
                    harness_metadata, timestamp, retry_count)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cell.cell_id,
                    cell.run_name,
                    cell.harness.name,
                    cell.model.name,
                    cell.task.id,
                    cell.task.track.value,
                    cell.repeat,
                    exit_class,
                    success,
                    error_class,
                    error_message,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cache_write_tokens,
                    usage.reasoning_tokens,
                    total_cost,
                    latency_ms,
                    time_to_first_attempt_ms,
                    num_api_calls,
                    num_tool_calls,
                    diff,
                    test_output,
                    json.dumps(harness_metadata) if harness_metadata else None,
                    datetime.now(UTC).isoformat(),
                    retry_count,
                ),
            )
            conn.commit()

    def get_result(self, cell_id: str) -> dict[str, Any] | None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM run_results WHERE cell_id = ?", (cell_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_results(self, run_name: str | None = None) -> list[dict[str, Any]]:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if run_name:
                rows = conn.execute(
                    "SELECT * FROM run_results WHERE run_name = ?"
                    " ORDER BY harness, model, task_id, repeat",
                    (run_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM run_results ORDER BY harness, model, task_id, repeat"
                ).fetchall()
            return [dict(row) for row in rows]

    def set_cell_state(
        self,
        cell_id: str,
        run_name: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            if status == "running":
                conn.execute(
                    """INSERT OR REPLACE INTO run_state
                       (cell_id, run_name, status, started_at, completed_at, error)
                       VALUES (?, ?, ?, ?, NULL, ?)""",
                    (cell_id, run_name, status, now, error),
                )
            elif status in ("completed", "failed", "skipped"):
                conn.execute(
                    """INSERT OR REPLACE INTO run_state
                       (cell_id, run_name, status, started_at, completed_at, error)
                       VALUES (?, ?, ?,
                        COALESCE((SELECT started_at FROM run_state WHERE cell_id = ?), ?),
                        ?, ?)""",
                    (cell_id, run_name, status, cell_id, now, now, error),
                )
            conn.commit()

    def get_cell_state(self, cell_id: str) -> str | None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM run_state WHERE cell_id = ?", (cell_id,)
            ).fetchone()
            return row[0] if row else None

    def get_completed_cells(self, run_name: str) -> set[str]:
        """Get set of cell IDs that have been completed (for resumability)."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT cell_id FROM run_state WHERE run_name = ? AND status = 'completed'",
                (run_name,),
            ).fetchall()
            return {row[0] for row in rows}

    def get_total_cost(self, run_name: str) -> float:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_cost), 0) FROM run_results WHERE run_name = ?",
                (run_name,),
            ).fetchone()
            return float(row[0]) if row[0] else 0.0

    def save_run_metadata(
        self,
        run_name: str,
        config_json: str,
        harness_evaluator_version: str | None = None,
        docker_image: str | None = None,
    ) -> None:
        """Save run metadata for reproducibility.

        Stores the full run config, harness-evaluator version, and Docker image
        so a run can be reproduced exactly.
        """
        now = datetime.now(UTC).isoformat()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO run_metadata
                   (run_name, config_json, harness_evaluator_version, docker_image, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_name, config_json, harness_evaluator_version, docker_image, now),
            )
            conn.commit()

    def get_run_metadata(self, run_name: str) -> dict[str, Any] | None:
        """Get run metadata for reproducibility."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM run_metadata WHERE run_name = ?", (run_name,)
            ).fetchone()
            return dict(row) if row else None
