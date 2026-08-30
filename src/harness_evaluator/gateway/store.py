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

    def save(self, call: CapturedCall) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
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
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM captured_calls ORDER BY timestamp").fetchall()
            return [self._row_to_call(row) for row in rows]

    def get_by_id(self, call_id: str) -> CapturedCall | None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM captured_calls WHERE id = ?", (call_id,)
            ).fetchone()
            return self._row_to_call(row) if row else None

    def get_by_trace(self, trace_id: str) -> list[CapturedCall]:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM captured_calls WHERE trace_id = ? ORDER BY timestamp",
                (trace_id,),
            ).fetchall()
            return [self._row_to_call(row) for row in rows]

    def delete_by_trace(self, trace_id: str) -> None:
        """Delete all captured calls for a trace ID.

        Used on re-runs to avoid double-counting tokens/cost from prior
        attempts of the same cell.
        """
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
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
