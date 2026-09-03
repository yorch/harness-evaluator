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
    cell_id TEXT NOT NULL,
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
    harness_stdout TEXT,
    harness_stderr TEXT,
    timestamp TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    cost_mode TEXT DEFAULT 'platform',
    PRIMARY KEY (run_name, cell_id)
);

CREATE INDEX IF NOT EXISTS idx_run_name ON run_results(run_name);
CREATE INDEX IF NOT EXISTS idx_harness_model ON run_results(harness, model);
CREATE INDEX IF NOT EXISTS idx_task ON run_results(task_id);

CREATE TABLE IF NOT EXISTS run_state (
    cell_id TEXT NOT NULL,
    run_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    phase TEXT,
    phase_started_at TEXT,
    PRIMARY KEY (run_name, cell_id)
);

CREATE INDEX IF NOT EXISTS idx_run_state_name ON run_state(run_name);

CREATE TABLE IF NOT EXISTS run_metadata (
    run_name TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    harness_evaluator_version TEXT,
    docker_image TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id TEXT NOT NULL,
    run_name TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    model TEXT NOT NULL,
    model_role TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    duration_ms REAL DEFAULT 0.0,
    timed_out INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    num_api_calls INTEGER DEFAULT 0,
    error TEXT,
    stdout TEXT,
    stderr TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (run_name, cell_id) REFERENCES run_results(run_name, cell_id)
);

CREATE INDEX IF NOT EXISTS idx_phase_cell ON phase_results(cell_id);
CREATE INDEX IF NOT EXISTS idx_phase_run ON phase_results(run_name);
CREATE INDEX IF NOT EXISTS idx_phase_run_cell ON phase_results(run_name, cell_id);

CREATE TABLE IF NOT EXISTS reconciliation_results (
    cell_id TEXT NOT NULL,
    run_name TEXT NOT NULL,
    proxy_usage_json TEXT,
    self_reported_usage_json TEXT,
    matched INTEGER NOT NULL,
    max_discrepancy_pct REAL NOT NULL,
    details_json TEXT,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (run_name, cell_id),
    FOREIGN KEY (run_name, cell_id) REFERENCES run_results(run_name, cell_id)
);

CREATE INDEX IF NOT EXISTS idx_recon_run ON reconciliation_results(run_name);
"""

# Columns added after initial schema; added via ALTER TABLE for existing DBs.
# ``_apply_migrations`` guards every entry with a ``PRAGMA table_info``
# existence check, so re-running this list against a DB that already has a
# column (e.g. one created fresh from ``SCHEMA``) is a no-op, not an error.
_MIGRATIONS = [
    ("run_results", "harness_stdout", "TEXT"),
    ("run_results", "harness_stderr", "TEXT"),
    ("phase_results", "stdout", "TEXT"),
    ("phase_results", "stderr", "TEXT"),
    ("run_results", "cost_mode", "TEXT DEFAULT 'platform'"),
    ("run_state", "phase", "TEXT"),
    ("run_state", "phase_started_at", "TEXT"),
]

# Tables whose primary key changed from a bare ``cell_id`` to a composite
# ``(run_name, cell_id)``. Maps table name to (new-table CREATE TABLE SQL,
# index-recreation statements to run once the rebuilt table has been renamed
# back into place).
_LEGACY_PK_REBUILD: dict[str, tuple[str, list[str]]] = {
    "run_results": (
        """
        CREATE TABLE run_results_new (
            cell_id TEXT NOT NULL,
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
            harness_stdout TEXT,
            harness_stderr TEXT,
            timestamp TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            cost_mode TEXT DEFAULT 'platform',
            PRIMARY KEY (run_name, cell_id)
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_run_name ON run_results(run_name)",
            "CREATE INDEX IF NOT EXISTS idx_harness_model ON run_results(harness, model)",
            "CREATE INDEX IF NOT EXISTS idx_task ON run_results(task_id)",
        ],
    ),
    "run_state": (
        """
        CREATE TABLE run_state_new (
            cell_id TEXT NOT NULL,
            run_name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error TEXT,
            phase TEXT,
            phase_started_at TEXT,
            PRIMARY KEY (run_name, cell_id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_run_state_name ON run_state(run_name)"],
    ),
    "reconciliation_results": (
        """
        CREATE TABLE reconciliation_results_new (
            cell_id TEXT NOT NULL,
            run_name TEXT NOT NULL,
            proxy_usage_json TEXT,
            self_reported_usage_json TEXT,
            matched INTEGER NOT NULL,
            max_discrepancy_pct REAL NOT NULL,
            details_json TEXT,
            timestamp TEXT NOT NULL,
            PRIMARY KEY (run_name, cell_id),
            FOREIGN KEY (run_name, cell_id) REFERENCES run_results(run_name, cell_id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_recon_run ON reconciliation_results(run_name)"],
    ),
    "phase_results": (
        """
        CREATE TABLE phase_results_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cell_id TEXT NOT NULL,
            run_name TEXT NOT NULL,
            phase_name TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            model TEXT NOT NULL,
            model_role TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            duration_ms REAL DEFAULT 0.0,
            timed_out INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_cost REAL DEFAULT 0.0,
            num_api_calls INTEGER DEFAULT 0,
            error TEXT,
            stdout TEXT,
            stderr TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (run_name, cell_id) REFERENCES run_results(run_name, cell_id)
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_phase_cell ON phase_results(cell_id)",
            "CREATE INDEX IF NOT EXISTS idx_phase_run ON phase_results(run_name)",
            "CREATE INDEX IF NOT EXISTS idx_phase_run_cell ON phase_results(run_name, cell_id)",
        ],
    ),
}


class ResultsStore:
    """SQLite-backed store for evaluation results."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(SCHEMA)
            self._rebuild_legacy_pk_tables(conn)
            self._apply_migrations(conn)
            conn.commit()

    @staticmethod
    def _default_literal_for(col_type: str) -> str:
        """A SQL literal to satisfy a ``NOT NULL`` column the legacy row can't supply.

        Used both when a legacy table predates a column the new schema
        requires, and when a legacy row holds NULL in a column the new
        schema marks ``NOT NULL``. No schema this project has ever shipped
        actually lacks or nulls these columns (the initial schema already
        declared e.g. ``timestamp`` and ``run_name`` as ``NOT NULL``); this
        exists to tolerate hand-rolled or third-party partial schemas, such
        as the fixtures in ``tests/test_smoke.py``, rather than bricking the
        store on them. Picks a literal by SQLite type affinity: ``0`` for
        integer/real/numeric columns, ``''`` for everything else (text and
        otherwise untyped).
        """
        affinity = col_type.upper()
        if any(token in affinity for token in ("INT", "REAL", "FLOA", "DOUB", "NUM")):
            return "0"
        return "''"

    @staticmethod
    def _phase_results_needs_rebuild(conn: sqlite3.Connection) -> bool:
        """Detect a ``phase_results`` table still on the legacy single-column FK.

        Unlike ``run_results``/``run_state``/``reconciliation_results``,
        ``phase_results`` keys off its own ``id`` (``INTEGER PRIMARY KEY
        AUTOINCREMENT``), not ``cell_id`` — so the "exactly one pk column
        named cell_id" check the other three tables use does not apply here.
        Instead this inspects ``PRAGMA foreign_key_list``: a legacy table has
        a single-column FK to ``run_results(cell_id)`` (one row); an
        already-migrated or freshly created table has the composite FK to
        ``run_results(run_name, cell_id)`` (two rows, one per referencing
        column).
        """
        fk_rows = conn.execute("PRAGMA foreign_key_list(phase_results)").fetchall()
        return len(fk_rows) == 1

    @staticmethod
    def _rebuild_legacy_pk_tables(conn: sqlite3.Connection) -> None:
        """Rebuild tables still using the legacy single-column ``cell_id`` key.

        Older databases (created before runs were made first-class) have
        ``run_results``, ``run_state``, and ``reconciliation_results`` keyed
        on ``cell_id`` alone, so two runs sharing a matrix cell overwrite
        each other's rows; and ``phase_results`` referencing ``run_results``
        by ``cell_id`` alone, which becomes an unsatisfiable FK once
        ``run_results`` no longer has a unique index on ``cell_id`` by
        itself. This detects the first three via ``PRAGMA table_info``
        (exactly one ``pk`` column, named ``cell_id``) and ``phase_results``
        via ``_phase_results_needs_rebuild`` (its PK is ``id``, not
        ``cell_id``, so the same check doesn't apply), then rebuilds each
        with the composite ``(run_name, cell_id)`` key, preserving every
        existing row (and, for ``phase_results``, its existing ``id``s).

        A table already on the composite key is left untouched, so calling
        this on every ``_init_db`` (including against a freshly created DB,
        whose tables come out of ``SCHEMA`` already on the new key) is a
        no-op. A legacy DB that already had two runs collide on the same
        ``cell_id`` cannot be un-collided here — whichever row is on disk is
        the one that survives the rebuild.

        ``PRAGMA foreign_keys`` is a no-op inside a transaction, so it is
        toggled off before ``BEGIN`` and restored to whatever it was
        beforehand (not unconditionally back on) once the transaction has
        committed or rolled back.
        """
        legacy_tables = []
        for table in _LEGACY_PK_REBUILD:
            if table == "phase_results":
                if ResultsStore._phase_results_needs_rebuild(conn):
                    legacy_tables.append(table)
                continue
            pk_columns = [
                row[1]
                for row in sorted(
                    conn.execute(f"PRAGMA table_info({table})").fetchall(),
                    key=lambda row: row[5],
                )
                if row[5] > 0
            ]
            if pk_columns == ["cell_id"]:
                legacy_tables.append(table)

        if not legacy_tables:
            return

        prior_foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            try:
                for table in legacy_tables:
                    new_table_sql, index_statements = _LEGACY_PK_REBUILD[table]
                    new_table = f"{table}_new"
                    conn.execute(new_table_sql)

                    old_columns = {
                        row[1]
                        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                    }
                    new_table_info = conn.execute(
                        f"PRAGMA table_info({new_table})"
                    ).fetchall()

                    # Columns present in both tables are copied verbatim,
                    # unless the new schema marks the column NOT NULL, in
                    # which case a COALESCE substitutes a literal placeholder
                    # for any NULL the legacy row might hold (a legacy DB can
                    # have the column but a NULL value in it — copying it
                    # verbatim would fail the NOT NULL constraint on INSERT).
                    # A column missing from the legacy table entirely is left
                    # out of the INSERT so it takes its declared default,
                    # unless the new schema marks it NOT NULL, in which case
                    # the same literal placeholder fills the gap outright.
                    insert_columns = []
                    select_exprs = []
                    for row in new_table_info:
                        name, col_type, notnull = row[1], row[2], row[3]
                        if name in old_columns:
                            insert_columns.append(name)
                            if notnull:
                                default_literal = ResultsStore._default_literal_for(col_type)
                                select_exprs.append(f"COALESCE({name}, {default_literal})")
                            else:
                                select_exprs.append(name)
                        elif notnull:
                            insert_columns.append(name)
                            select_exprs.append(
                                ResultsStore._default_literal_for(col_type)
                            )

                    insert_cols_sql = ", ".join(insert_columns)
                    select_exprs_sql = ", ".join(select_exprs)
                    conn.execute(
                        f"INSERT INTO {new_table} ({insert_cols_sql}) "
                        f"SELECT {select_exprs_sql} FROM {table}"
                    )
                    conn.execute(f"DROP TABLE {table}")
                    conn.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
                    for statement in index_statements:
                        conn.execute(statement)
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()
        finally:
            conn.execute(f"PRAGMA foreign_keys = {prior_foreign_keys}")

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        """Add columns that were introduced after the initial schema.

        Uses ``PRAGMA table_info`` to check for column existence before
        running ``ALTER TABLE``, so existing databases are upgraded in
        place without data loss.
        """
        for table, column, col_type in _MIGRATIONS:
            existing = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                )

    def list_runs(self) -> list[dict[str, Any]]:
        """List all run names with aggregate stats (cell counts, success rate).

        Returns a list of dicts sorted by run name, each containing:
        ``run_name``, ``total_cells``, ``completed``, ``failed``,
        ``avg_success``, ``total_cost``.
        """
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT
                       run_name,
                       COUNT(*) AS total_cells,
                       SUM(CASE WHEN exit_class = 'success' THEN 1 ELSE 0 END)
                           AS completed,
                       SUM(CASE WHEN exit_class NOT IN ('success', 'skipped')
                                THEN 1 ELSE 0 END) AS failed,
                       AVG(success) AS avg_success,
                       SUM(total_cost) AS total_cost
                   FROM run_results
                   GROUP BY run_name
                   ORDER BY run_name"""
            ).fetchall()
            return [dict(row) for row in rows]

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
        harness_stdout: str | None = None,
        harness_stderr: str | None = None,
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
                    harness_metadata, harness_stdout, harness_stderr,
                    timestamp, retry_count, cost_mode)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    harness_stdout,
                    harness_stderr,
                    datetime.now(UTC).isoformat(),
                    retry_count,
                    cell.model.cost_mode.value,
                ),
            )
            conn.commit()

    def get_result(self, cell_id: str, run_name: str | None = None) -> dict[str, Any] | None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if run_name is not None:
                row = conn.execute(
                    "SELECT * FROM run_results WHERE cell_id = ? AND run_name = ?",
                    (cell_id, run_name),
                ).fetchone()
            else:
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
                        COALESCE(
                            (SELECT started_at FROM run_state
                             WHERE cell_id = ? AND run_name = ?),
                            ?),
                        ?, ?)""",
                    (cell_id, run_name, status, cell_id, run_name, now, now, error),
                )
            conn.commit()

    def get_cell_state(self, cell_id: str, run_name: str | None = None) -> str | None:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            if run_name is not None:
                row = conn.execute(
                    "SELECT status FROM run_state WHERE cell_id = ? AND run_name = ?",
                    (cell_id, run_name),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT status FROM run_state WHERE cell_id = ?", (cell_id,)
                ).fetchone()
            return row[0] if row else None

    def set_cell_phase(
        self, cell_id: str, run_name: str, phase: str
    ) -> None:
        """Update the current execution phase for a running cell.

        Called by ``DockerRunner`` at each internal phase transition
        (cloning, container_start, setup, harness_running, evaluating,
        aggregating, reconciling). The TUI polls this on its 1-second
        tick timer to show per-cell phase labels without threading
        callbacks through the orchestrator.
        """
        now = datetime.now(UTC).isoformat()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE run_state SET phase = ?, phase_started_at = ? "
                "WHERE cell_id = ? AND run_name = ?",
                (phase, now, cell_id, run_name),
            )
            conn.commit()

    def get_running_cell_phases(
        self, run_name: str
    ) -> dict[str, tuple[str | None, str | None]]:
        """Return ``{cell_id: (phase, phase_started_at)}`` for all running cells.

        Used by the TUI's tick timer to poll phase state without
        callbacks. Returns an empty dict if no cells are running or
        the table/columns do not exist yet (pre-migration DBs).
        """
        try:
            with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT cell_id, phase, phase_started_at "
                    "FROM run_state WHERE run_name = ? AND status = 'running'",
                    (run_name,),
                ).fetchall()
                return {
                    row["cell_id"]: (row["phase"], row["phase_started_at"])
                    for row in rows
                }
        except sqlite3.OperationalError:
            return {}

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

    def get_billable_cost(self, run_name: str) -> float:
        """Sum billable cost for a run, excluding subscription-mode cells.

        Unlike ``get_total_cost`` (the "what this would have cost" figure),
        this excludes rows whose ``cost_mode`` is ``'subscription'`` — those
        ran under a zero-dollar, token-only accounting mode. A NULL
        ``cost_mode`` (legacy rows) is treated as billable.
        """
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(total_cost), 0) FROM run_results
                   WHERE run_name = ?
                     AND (cost_mode IS NULL OR cost_mode != 'subscription')""",
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

    def save_phase_results(
        self,
        cell_id: str,
        run_name: str,
        phases: list[dict[str, Any]],
    ) -> None:
        """Save per-phase results for a multi-phase cell.

        Each phase dict should contain: name, trace_id, model,
        model_role, exit_code, duration_ms, timed_out, and optionally
        usage/cost fields (input_tokens, output_tokens, total_cost,
        num_api_calls).
        """
        now = datetime.now(UTC).isoformat()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            # Clear prior phase results for this (run_name, cell_id) (resumability).
            conn.execute(
                "DELETE FROM phase_results WHERE cell_id = ? AND run_name = ?",
                (cell_id, run_name),
            )
            for phase in phases:
                usage = phase.get("usage")
                # usage can be a TokenUsage object or a plain dict
                if usage is None:
                    in_tok = out_tok = 0
                elif hasattr(usage, "input_tokens"):
                    in_tok = usage.input_tokens
                    out_tok = usage.output_tokens
                else:
                    in_tok = usage.get("input_tokens", 0)
                    out_tok = usage.get("output_tokens", 0)
                conn.execute(
                    """INSERT INTO phase_results
                       (cell_id, run_name, phase_name, trace_id, model,
                        model_role, exit_code, duration_ms, timed_out,
                        input_tokens, output_tokens, total_cost,
                        num_api_calls, error, stdout, stderr, timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cell_id,
                        run_name,
                        phase["name"],
                        phase["trace_id"],
                        phase["model"],
                        phase.get("model_role", "implementation"),
                        phase["exit_code"],
                        phase.get("duration_ms", 0.0),
                        1 if phase.get("timed_out") else 0,
                        in_tok,
                        out_tok,
                        phase.get("total_cost", 0.0),
                        phase.get("num_api_calls", 0),
                        phase.get("error"),
                        phase.get("stdout"),
                        phase.get("stderr"),
                        now,
                    ),
                )
            conn.commit()

    def get_phase_results(
        self, cell_id: str, run_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Get per-phase results for a cell."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if run_name is not None:
                rows = conn.execute(
                    "SELECT * FROM phase_results WHERE cell_id = ? AND run_name = ?"
                    " ORDER BY id ASC",
                    (cell_id, run_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM phase_results WHERE cell_id = ?"
                    " ORDER BY id ASC",
                    (cell_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    def save_reconciliation_result(
        self,
        cell_id: str,
        run_name: str,
        proxy_usage: TokenUsage | None = None,
        self_reported_usage: TokenUsage | None = None,
        matched: bool = True,
        max_discrepancy_pct: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Save a reconciliation result for a cell.

        Args:
            cell_id: The cell ID this result belongs to.
            run_name: The run name.
            proxy_usage: Usage captured by the gateway proxy.
            self_reported_usage: Usage self-reported by the harness.
            matched: Whether the sources reconciled within tolerance.
            max_discrepancy_pct: Largest percentage discrepancy across fields.
            details: Per-field breakdown of discrepancies.
        """
        now = datetime.now(UTC).isoformat()
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO reconciliation_results
                   (cell_id, run_name, proxy_usage_json,
                    self_reported_usage_json, matched, max_discrepancy_pct,
                    details_json, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cell_id,
                    run_name,
                    proxy_usage.model_dump_json() if proxy_usage else None,
                    (
                        self_reported_usage.model_dump_json()
                        if self_reported_usage
                        else None
                    ),
                    1 if matched else 0,
                    max_discrepancy_pct,
                    json.dumps(details) if details else None,
                    now,
                ),
            )
            conn.commit()

    def get_reconciliation_result(
        self, cell_id: str, run_name: str | None = None
    ) -> dict[str, Any] | None:
        """Get the reconciliation result for a cell."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if run_name is not None:
                row = conn.execute(
                    "SELECT * FROM reconciliation_results WHERE cell_id = ? AND run_name = ?",
                    (cell_id, run_name),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM reconciliation_results WHERE cell_id = ?",
                    (cell_id,),
                ).fetchone()
            return dict(row) if row else None
