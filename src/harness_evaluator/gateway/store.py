"""Storage for captured gateway calls.

Uses SQLite for structured queryability and JSON for raw request/response bodies.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from harness_evaluator.gateway.models import CapturedCall

SCHEMA = """
CREATE TABLE IF NOT EXISTS captured_calls (
    id TEXT PRIMARY KEY,
    trace_id TEXT,
    parent_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    request_headers TEXT,
    request_body TEXT,
    response_status INTEGER,
    response_headers TEXT,
    response_body TEXT,
    usage_json TEXT,
    cost_json TEXT,
    latency_ms REAL,
    timestamp TEXT,
    is_streaming INTEGER,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_trace_id ON captured_calls(trace_id);
CREATE INDEX IF NOT EXISTS idx_provider_model ON captured_calls(provider, model);
CREATE INDEX IF NOT EXISTS idx_timestamp ON captured_calls(timestamp);
"""


class CallStore:
    """SQLite-backed store for captured API calls."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with a busy timeout for concurrent access.

        The stats summary loop reads concurrently with request-handler
        writes. Without a busy timeout, SQLite returns
        ``OperationalError: database is locked`` immediately on contention.
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, call: CapturedCall) -> None:
        try:
            self._save(call)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                # The table is missing — the DB file may have been
                # deleted and recreated (e.g. by an external process
                # or a concurrent CallStore.__init__ race). Reinitialize
                # the schema and retry once.
                self._init_db()
                self._save(call)
            else:
                raise

    def _save(self, call: CapturedCall) -> None:
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO captured_calls
                   (id, trace_id, parent_id, provider, model, method, path,
                    request_headers, request_body, response_status, response_headers,
                    response_body, usage_json, cost_json, latency_ms, timestamp,
                    is_streaming, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    call.id,
                    call.trace_id,
                    call.parent_id,
                    call.provider.value,
                    call.model,
                    call.method,
                    call.path,
                    json.dumps(call.request_headers),
                    json.dumps(call.request_body) if call.request_body else None,
                    call.response_status,
                    json.dumps(call.response_headers),
                    json.dumps(call.response_body) if call.response_body else None,
                    call.usage.model_dump_json(),
                    call.cost.model_dump_json(),
                    call.latency_ms,
                    call.timestamp.isoformat(),
                    int(call.is_streaming),
                    call.error,
                ),
            )
            conn.commit()

    def get_all(self) -> list[CapturedCall]:
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM captured_calls ORDER BY timestamp").fetchall()
            return [self._row_to_call(row) for row in rows]

    def get_by_id(self, call_id: str) -> CapturedCall | None:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM captured_calls WHERE id = ?", (call_id,)
            ).fetchone()
            return self._row_to_call(row) if row else None

    def get_by_trace(self, trace_id: str) -> list[CapturedCall]:
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM captured_calls WHERE trace_id = ? ORDER BY timestamp",
                (trace_id,),
            ).fetchall()
            return [self._row_to_call(row) for row in rows]

    def get_by_trace_prefix(self, trace_prefix: str) -> list[CapturedCall]:
        """Return calls whose trace_id starts with ``trace_prefix``.

        Used by the TUI to aggregate API calls for multi-phase cells,
        where each phase has a trace ID like ``{cell_id}__phase-{name}``.
        """
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM captured_calls WHERE trace_id LIKE ? ORDER BY timestamp",
                (f"{trace_prefix}%",),
            ).fetchall()
            return [self._row_to_call(row) for row in rows]

    def get_stats_summary(self) -> dict[str, int | float]:
        """Return aggregated stats over all captured calls.

        Computes totals (call count, cost, tokens, latency) in a single
        SQL pass so the periodic stats loop does not need to load every
        row into Python. Each ``json_extract`` is wrapped in ``COALESCE``
        and guarded by ``json_valid`` so that rows with missing fields or
        malformed JSON are treated as zero rather than producing NULL
        propagation (which would silently exclude the entire row from the
        sum) or raising ``OperationalError``.
        """
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT
                       COUNT(*) AS total_calls,
                       COALESCE(SUM(
                           COALESCE(IIF(json_valid(cost_json),
                               json_extract(cost_json, '$.input_cost'), 0), 0)
                           + COALESCE(IIF(json_valid(cost_json),
                               json_extract(cost_json, '$.output_cost'), 0), 0)
                           + COALESCE(IIF(json_valid(cost_json),
                               json_extract(cost_json, '$.cache_read_cost'), 0), 0)
                           + COALESCE(IIF(json_valid(cost_json),
                               json_extract(cost_json, '$.cache_write_cost'), 0), 0)
                           + COALESCE(IIF(json_valid(cost_json),
                               json_extract(cost_json, '$.reasoning_cost'), 0), 0)
                       ), 0) AS total_cost,
                       COALESCE(SUM(
                           COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.input_tokens'), 0), 0)
                       ), 0) AS total_input_tokens,
                       COALESCE(SUM(
                           COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.output_tokens'), 0), 0)
                       ), 0) AS total_output_tokens,
                       COALESCE(SUM(
                           COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.input_tokens'), 0), 0)
                           + COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.output_tokens'), 0), 0)
                           + COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.cache_read_tokens'), 0), 0)
                           + COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.cache_write_tokens'), 0), 0)
                           + COALESCE(IIF(json_valid(usage_json),
                               json_extract(usage_json, '$.reasoning_tokens'), 0), 0)
                       ), 0) AS total_tokens,
                       COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                   FROM captured_calls""",
            ).fetchone()
        return {
            "total_calls": int(row[0] or 0),
            "total_cost": float(row[1] or 0.0),
            "total_input_tokens": int(row[2] or 0),
            "total_output_tokens": int(row[3] or 0),
            "total_tokens": int(row[4] or 0),
            "avg_latency_ms": float(row[5] or 0.0),
        }

    def get_calls_since(self, since_iso: str) -> int:
        """Return the number of calls captured since an ISO timestamp."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM captured_calls WHERE timestamp > ?",
                (since_iso,),
            ).fetchone()
        return int(row[0] or 0)

    def delete_by_trace(self, trace_id: str) -> None:
        """Delete all captured calls for a trace ID.

        Used on re-runs to avoid double-counting tokens/cost from prior
        attempts of the same cell.
        """
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM captured_calls WHERE trace_id = ?", (trace_id,)
            )
            conn.commit()

    def _row_to_call(self, row: sqlite3.Row) -> CapturedCall:
        from harness_evaluator.gateway.models import CostBreakdown, Provider, TokenUsage

        def _parse_json(value: Any) -> dict[str, Any] | None:
            if value is None:
                return None
            if isinstance(value, str):
                parsed: dict[str, Any] = json.loads(value)
                return parsed
            return dict(value)

        usage_data = _parse_json(row["usage_json"]) or {}
        cost_data = _parse_json(row["cost_json"]) or {}

        return CapturedCall(
            id=row["id"],
            trace_id=row["trace_id"],
            parent_id=row["parent_id"],
            provider=Provider(row["provider"]),
            model=row["model"],
            method=row["method"],
            path=row["path"],
            request_headers=_parse_json(row["request_headers"]) or {},
            request_body=_parse_json(row["request_body"]),
            response_status=row["response_status"],
            response_headers=_parse_json(row["response_headers"]) or {},
            response_body=_parse_json(row["response_body"]),
            usage=TokenUsage(**usage_data),
            cost=CostBreakdown(**cost_data),
            latency_ms=row["latency_ms"],
            timestamp=row["timestamp"],
            is_streaming=bool(row["is_streaming"]),
            error=row["error"],
        )
