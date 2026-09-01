"""Tests that the results store isolates rows by run_name, not just cell_id.

Two eval runs that happen to share a matrix cell (same harness/model/task/repeat)
produce the same ``cell_id``. Before the composite ``(run_name, cell_id)`` primary
key, one run's row for that cell would silently clobber the other's.
"""

from __future__ import annotations

import sqlite3

import pytest

from harness_evaluator.orchestrator.config import (
    CostMode,
    HarnessSpec,
    ModelSpec,
    RunCell,
    TaskSpec,
    TaskTrack,
)
from harness_evaluator.orchestrator.results_store import ResultsStore


@pytest.fixture
def store(tmp_path):
    return ResultsStore(str(tmp_path / "test_results.db"))


def _make_cell(run_name: str, cost_mode: CostMode = CostMode.PLATFORM) -> RunCell:
    return RunCell(
        run_name=run_name,
        harness=HarnessSpec(name="opencode", adapter="opencode"),
        model=ModelSpec(
            name="claude-sonnet-4-20250514",
            provider="anthropic",
            api_key_env="X",
            cost_mode=cost_mode,
        ),
        task=TaskSpec(
            id="task-1",
            name="Task 1",
            track=TaskTrack.SWE,
            task_prompt="Fix bug",
        ),
        repeat=0,
    )


class TestRunIsolation:
    def test_two_runs_sharing_a_cell_id_both_survive(self, store):
        """The core regression: two runs writing the same cell_id must not collide.

        Both cells here have identical harness/model/task/repeat, so they share
        the exact same ``cell_id`` — only ``run_name`` differs. On the pre-change
        schema (single-column ``cell_id`` PRIMARY KEY), the second ``save_result``
        call overwrites the first run's row entirely.
        """
        cell_a = _make_cell("run-a")
        cell_b = _make_cell("run-b")
        assert cell_a.cell_id == cell_b.cell_id

        store.save_result(
            cell=cell_a,
            exit_class="pass",
            success=1.0,
            total_cost=1.0,
        )
        store.save_result(
            cell=cell_b,
            exit_class="fail",
            success=0.0,
            total_cost=2.0,
        )

        # Both rows must exist independently: on the pre-change single-column
        # cell_id PRIMARY KEY, the second INSERT OR REPLACE clobbers the
        # first, leaving only one row for the shared cell_id.
        all_rows = [r for r in store.get_all_results() if r["cell_id"] == cell_a.cell_id]
        assert len(all_rows) == 2, (
            f"expected 2 independent rows for shared cell_id, got {len(all_rows)}"
        )

        result_a = store.get_result(cell_a.cell_id, run_name="run-a")
        result_b = store.get_result(cell_b.cell_id, run_name="run-b")
        assert result_a is not None
        assert result_b is not None
        assert result_a["run_name"] == "run-a"
        assert result_a["exit_class"] == "pass"
        assert result_a["total_cost"] == pytest.approx(1.0)
        assert result_b["run_name"] == "run-b"
        assert result_b["exit_class"] == "fail"
        assert result_b["total_cost"] == pytest.approx(2.0)

        assert store.get_total_cost("run-a") == pytest.approx(1.0)
        assert store.get_total_cost("run-b") == pytest.approx(2.0)

        store.set_cell_state(cell_a.cell_id, "run-a", "completed")
        store.set_cell_state(cell_b.cell_id, "run-b", "completed")
        assert store.get_completed_cells("run-a") == {cell_a.cell_id}
        assert store.get_completed_cells("run-b") == {cell_b.cell_id}

    def test_set_cell_state_preserves_started_at_per_run(self, store):
        """started_at must be tracked per (run_name, cell_id), not globally.

        run-a starts (and gets a started_at). run-b then completes the
        *same* cell_id without ever having been marked "running" itself.
        On the pre-change code, the COALESCE subquery filtered on cell_id
        alone, so run-b would incorrectly inherit run-a's started_at. With
        the fix, run-b finds no prior row for (run-b, cell_id) and falls
        back to "now" for both started_at and completed_at within the same
        call, so the two must be identical for run-b and distinct from
        run-a's started_at.
        """
        cell_id = _make_cell("run-a").cell_id

        store.set_cell_state(cell_id, "run-a", "running")

        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            started_a = conn.execute(
                "SELECT started_at FROM run_state WHERE run_name = ? AND cell_id = ?",
                ("run-a", cell_id),
            ).fetchone()["started_at"]

        # run-b completes the same cell_id without ever running it itself.
        store.set_cell_state(cell_id, "run-b", "completed")

        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row_b = conn.execute(
                "SELECT started_at, completed_at FROM run_state"
                " WHERE run_name = ? AND cell_id = ?",
                ("run-b", cell_id),
            ).fetchone()

        assert row_b["started_at"] == row_b["completed_at"]
        assert row_b["started_at"] != started_a

        assert store.get_cell_state(cell_id, run_name="run-a") == "running"
        assert store.get_cell_state(cell_id, run_name="run-b") == "completed"

    def test_save_phase_results_does_not_delete_other_runs_phases(self, store):
        """``save_phase_results`` must scope its "clear prior phases" DELETE.

        Regression test for the Critical finding: the DELETE used to filter
        on ``cell_id`` alone (``DELETE FROM phase_results WHERE cell_id = ?``),
        so saving phases for run-b — which shares a ``cell_id`` with run-a —
        silently destroyed every one of run-a's phase rows, irrecoverably.
        """
        cell_id = _make_cell("run-a").cell_id

        store.save_phase_results(
            cell_id,
            "run-a",
            [
                {
                    "name": "impl",
                    "trace_id": "trace-a",
                    "model": "m",
                    "exit_code": 0,
                    "total_cost": 1.0,
                }
            ],
        )
        store.save_phase_results(
            cell_id,
            "run-b",
            [
                {
                    "name": "impl",
                    "trace_id": "trace-b",
                    "model": "m",
                    "exit_code": 0,
                    "total_cost": 2.0,
                }
            ],
        )

        phases_a = store.get_phase_results(cell_id, run_name="run-a")
        phases_b = store.get_phase_results(cell_id, run_name="run-b")
        assert len(phases_a) == 1, f"run-a's phase rows were destroyed by run-b: {phases_a}"
        assert phases_a[0]["trace_id"] == "trace-a"
        assert len(phases_b) == 1
        assert phases_b[0]["trace_id"] == "trace-b"

        # A resumed run-a must only clear its own prior rows, not run-b's.
        store.save_phase_results(
            cell_id,
            "run-a",
            [
                {
                    "name": "impl",
                    "trace_id": "trace-a2",
                    "model": "m",
                    "exit_code": 0,
                    "total_cost": 1.5,
                }
            ],
        )
        phases_a2 = store.get_phase_results(cell_id, run_name="run-a")
        assert len(phases_a2) == 1
        assert phases_a2[0]["trace_id"] == "trace-a2"
        assert len(store.get_phase_results(cell_id, run_name="run-b")) == 1

    def test_pre_change_single_column_pk_actually_collides(self, tmp_path):
        """Proves ``test_two_runs_sharing_a_cell_id_both_survive`` is a real regression test.

        Part 1 builds the OLD schema directly (single-column
        ``cell_id TEXT PRIMARY KEY``, no ``run_name`` in the key) and
        performs the same two "different run, same cell_id" writes
        ``save_result`` would have issued against that schema (``INSERT OR
        REPLACE``). Unlike the composite-PK store, this must collide: only
        the second run's row survives.

        Part 1 alone doesn't exercise any production code — it would pass
        even with the whole ``harness_evaluator`` package deleted, so it is
        documentation, not a regression test on its own (an adversarial
        review flagged exactly this). Part 2 ties it to the real,
        currently-shipped migration: it opens the same file with the real
        ``ResultsStore`` (which runs ``_rebuild_legacy_pk_tables``) and then
        performs a *third* run's write for the same ``cell_id`` through the
        real ``save_result``. If the migration is broken, missing, or
        deleted, that write either collides with run-b's already-migrated
        row (this table stays single-PK) or raises — so this half fails if
        the fix regresses, unlike part 1.
        """
        cell_id = _make_cell("run-a").cell_id

        # Full enough for SCHEMA's CREATE INDEX statements (on harness/model
        # and task_id) to succeed when ResultsStore opens this file in part
        # 2 below — SCHEMA's ``CREATE TABLE IF NOT EXISTS`` leaves this
        # table's columns untouched since it already exists.
        db_path = str(tmp_path / "old_schema.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """CREATE TABLE run_results (
                       cell_id TEXT PRIMARY KEY,
                       run_name TEXT NOT NULL,
                       harness TEXT NOT NULL,
                       model TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       track TEXT NOT NULL,
                       repeat INTEGER NOT NULL,
                       exit_class TEXT NOT NULL,
                       success REAL NOT NULL,
                       total_cost REAL DEFAULT 0.0,
                       timestamp TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """INSERT OR REPLACE INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success, total_cost, timestamp)
                   VALUES (?, ?, 'opencode', 'claude-sonnet-4-20250514', 'task-1',
                           'swe', 0, ?, 1.0, ?, '2024-01-01T00:00:00+00:00')""",
                (cell_id, "run-a", "pass", 1.0),
            )
            conn.execute(
                """INSERT OR REPLACE INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success, total_cost, timestamp)
                   VALUES (?, ?, 'opencode', 'claude-sonnet-4-20250514', 'task-1',
                           'swe', 0, ?, 0.0, ?, '2024-01-01T00:00:00+00:00')""",
                (cell_id, "run-b", "fail", 2.0),
            )
            conn.commit()

            rows = conn.execute(
                "SELECT run_name, exit_class, total_cost FROM run_results WHERE cell_id = ?",
                (cell_id,),
            ).fetchall()

        # Part 1: the whole point of the single-column PK. The second write
        # clobbers the first. Only run-b's row is left.
        assert len(rows) == 1, (
            "expected the legacy single-column cell_id PK to collide the two "
            f"runs' rows into one, got {len(rows)} rows: {rows}"
        )
        assert rows[0] == ("run-b", "fail", pytest.approx(2.0))

        # Part 2: open with the real ResultsStore, which migrates this table
        # (via the production _rebuild_legacy_pk_tables) to the composite
        # (run_name, cell_id) PK, then write a third run through the real
        # save_result for the same cell_id. This must now coexist with
        # run-b's (already-migrated, pre-existing) row rather than collide
        # with it — proving the fix through the actual shipped code path,
        # not just by construction as in part 1.
        store = ResultsStore(db_path)
        cell_c = _make_cell("run-c")
        assert cell_c.cell_id == cell_id
        store.save_result(cell=cell_c, exit_class="pass", success=1.0, total_cost=3.0)

        result_b = store.get_result(cell_id, run_name="run-b")
        result_c = store.get_result(cell_id, run_name="run-c")
        assert result_b is not None, "migration lost run-b's pre-existing row"
        assert result_b["exit_class"] == "fail"
        assert result_c is not None, "run-c's write did not survive the migrated schema"
        assert result_c["exit_class"] == "pass"
        assert result_c["total_cost"] == pytest.approx(3.0)


class TestLegacySchemaMigration:
    def _create_legacy_db(self, db_path: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE run_results (
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
                CREATE TABLE run_state (
                    cell_id TEXT PRIMARY KEY,
                    run_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT
                );
                CREATE TABLE reconciliation_results (
                    cell_id TEXT NOT NULL PRIMARY KEY,
                    run_name TEXT NOT NULL,
                    proxy_usage_json TEXT,
                    self_reported_usage_json TEXT,
                    matched INTEGER NOT NULL,
                    max_discrepancy_pct REAL NOT NULL,
                    details_json TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (cell_id) REFERENCES run_results(cell_id)
                );
                CREATE TABLE phase_results (
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
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (cell_id) REFERENCES run_results(cell_id)
                );
                """
            )
            conn.execute(
                """INSERT INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success, timestamp)
                   VALUES ('cell-x', 'legacy-run', 'opencode', 'm', 'task-1',
                           'swe', 0, 'pass', 1.0, '2024-01-01T00:00:00+00:00')"""
            )
            conn.execute(
                """INSERT INTO run_state
                   (cell_id, run_name, status, started_at, completed_at, error)
                   VALUES ('cell-x', 'legacy-run', 'completed',
                           '2024-01-01T00:00:00+00:00', '2024-01-01T00:01:00+00:00', NULL)"""
            )
            conn.execute(
                """INSERT INTO reconciliation_results
                   (cell_id, run_name, matched, max_discrepancy_pct, timestamp)
                   VALUES ('cell-x', 'legacy-run', 1, 0.0, '2024-01-01T00:00:00+00:00')"""
            )
            conn.execute(
                """INSERT INTO phase_results
                   (id, cell_id, run_name, phase_name, trace_id, model, model_role,
                    exit_code, timestamp)
                   VALUES (42, 'cell-x', 'legacy-run', 'impl', 'trace-1', 'm',
                           'implementation', 0, '2024-01-01T00:00:00+00:00')"""
            )
            conn.commit()

    def test_legacy_pk_migration_preserves_rows_and_rebuilds_pk(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        self._create_legacy_db(db_path)

        store = ResultsStore(db_path)

        with sqlite3.connect(db_path) as conn:
            for table in ("run_results", "run_state", "reconciliation_results"):
                pk_cols = sorted(
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                    if row[5] > 0
                )
                assert pk_cols == ["cell_id", "run_name"], f"{table} PK not rebuilt"

        result = store.get_result("cell-x", run_name="legacy-run")
        assert result is not None
        assert result["exit_class"] == "pass"
        assert result["success"] == pytest.approx(1.0)

        assert store.get_cell_state("cell-x", run_name="legacy-run") == "completed"

        recon = store.get_reconciliation_result("cell-x", run_name="legacy-run")
        assert recon is not None
        assert recon["matched"] == 1

    def test_legacy_pk_migration_rebuilds_phase_results_fk_and_preserves_ids(self, tmp_path):
        """``phase_results`` must also be rebuilt, not just the three PK tables.

        Its legacy FK (``FOREIGN KEY (cell_id) REFERENCES run_results(cell_id)``)
        becomes unsatisfiable once ``run_results`` no longer has a unique index
        on ``cell_id`` alone — ``PRAGMA foreign_key_check`` reports a mismatch
        against an unrebuilt migrated DB even though a freshly created DB is
        clean. The rebuilt table must also preserve the existing
        autoincrement ``id`` values (unlike the PK tables, ``phase_results``
        keys off ``id``, not ``(run_name, cell_id)``).
        """
        db_path = str(tmp_path / "legacy.db")
        self._create_legacy_db(db_path)

        ResultsStore(db_path)

        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            mismatches = conn.execute("PRAGMA foreign_key_check(phase_results)").fetchall()
            assert mismatches == [], f"phase_results FK still broken: {mismatches}"

            fk_columns = conn.execute("PRAGMA foreign_key_list(phase_results)").fetchall()
            assert len(fk_columns) == 2, (
                f"expected a composite (run_name, cell_id) FK, got {fk_columns}"
            )

            row = conn.execute(
                "SELECT id, cell_id, run_name, phase_name, trace_id FROM phase_results"
            ).fetchone()
            assert row == (42, "cell-x", "legacy-run", "impl", "trace-1"), (
                "phase_results row was not preserved (or its id was not kept) "
                f"across the rebuild: {row}"
            )

    def test_legacy_pk_migration_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        self._create_legacy_db(db_path)

        ResultsStore(db_path)
        # Re-opening a second time must not raise and must not lose data.
        store2 = ResultsStore(db_path)

        result = store2.get_result("cell-x", run_name="legacy-run")
        assert result is not None
        assert result["exit_class"] == "pass"

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM run_results").fetchone()[0]
            assert count == 1

    def test_legacy_table_missing_not_null_column_still_migrates(self, tmp_path):
        """A legacy table can predate a column the new schema marks NOT NULL.

        Regression test for the ``results_new.timestamp`` NOT NULL failure:
        this ``run_results`` table has no ``timestamp`` column at all (it
        predates it), unlike ``_create_legacy_db`` above which always has
        one. The rebuild must fill the gap with a placeholder rather than
        omitting the column from the INSERT column list, and every
        surviving row must still be intact and queryable afterwards.
        """
        db_path = str(tmp_path / "legacy_no_timestamp.db")
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE run_results (
                    cell_id TEXT PRIMARY KEY,
                    run_name TEXT NOT NULL,
                    harness TEXT NOT NULL,
                    model TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    track TEXT NOT NULL,
                    repeat INTEGER NOT NULL,
                    exit_class TEXT NOT NULL,
                    success REAL NOT NULL
                );
                """
            )
            conn.execute(
                """INSERT INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success)
                   VALUES ('cell-y', 'legacy-run', 'opencode', 'm', 'task-1',
                           'swe', 0, 'pass', 1.0)"""
            )
            conn.commit()

        store = ResultsStore(db_path)

        with sqlite3.connect(db_path) as conn:
            pk_cols = sorted(
                row[1]
                for row in conn.execute("PRAGMA table_info(run_results)").fetchall()
                if row[5] > 0
            )
            assert pk_cols == ["cell_id", "run_name"]

        result = store.get_result("cell-y", run_name="legacy-run")
        assert result is not None
        assert result["exit_class"] == "pass"
        assert result["success"] == pytest.approx(1.0)
        # The missing NOT NULL column takes the literal placeholder, not NULL.
        assert result["timestamp"] == ""

    def test_legacy_row_with_null_in_a_not_null_column_still_migrates(self, tmp_path):
        """A legacy row can hold NULL in a column present in both schemas.

        Regression test: the fallback previously only handled a column
        *absent* from the legacy table (via ``_default_literal_for``), not
        one that is present but holds NULL for a given row. Reproduces the
        exact schema shape ``tests/test_smoke.py``'s own fixtures build
        (``run_name``/``success`` declared nullable, unlike the new schema's
        ``NOT NULL``) with one row whose ``success`` is NULL. Before the fix
        this raised ``IntegrityError`` inside ``ResultsStore.__init__`` on
        every open, permanently bricking the CLI and dashboard against that
        DB (the rollback left no data loss, but the store could never open).
        """
        db_path = str(tmp_path / "legacy_null_success.db")
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE run_results (
                    run_name TEXT, cell_id TEXT PRIMARY KEY,
                    harness TEXT, model TEXT, task_id TEXT, track TEXT,
                    repeat INTEGER, exit_class TEXT, success REAL,
                    timestamp TEXT
                );
                """
            )
            conn.execute(
                """INSERT INTO run_results
                   (run_name, cell_id, harness, model, task_id, track,
                    repeat, exit_class, success, timestamp)
                   VALUES ('legacy-run', 'cell-null', 'opencode', 'm', 'task-1',
                           'swe', 0, 'pass', NULL, '2024-01-01T00:00:00+00:00')"""
            )
            conn.commit()

        # Must not raise. Before the fix, this construction alone raised
        # IntegrityError, and every subsequent ResultsStore() on this file
        # (i.e. every CLI/dashboard invocation) raised the same way.
        store = ResultsStore(db_path)

        with sqlite3.connect(db_path) as conn:
            pk_cols = sorted(
                row[1]
                for row in conn.execute("PRAGMA table_info(run_results)").fetchall()
                if row[5] > 0
            )
            assert pk_cols == ["cell_id", "run_name"]
            count = conn.execute("SELECT COUNT(*) FROM run_results").fetchone()[0]
            assert count == 1

        result = store.get_result("cell-null", run_name="legacy-run")
        assert result is not None
        assert result["exit_class"] == "pass"
        # The NULL was coalesced to the literal placeholder, not left NULL
        # (which would have violated the new schema's NOT NULL) or dropped.
        assert result["success"] == pytest.approx(0.0)


class TestForeignKeysPragmaRestoration:
    """Gap 2: ``PRAGMA foreign_keys`` must be restored to its *prior* value.

    The pragma is per-connection, not persisted in the database file, so it
    cannot be observed on a fresh connection after ``ResultsStore()``
    returns — that new connection always starts at SQLite's own default.
    The only place the "restore, don't blindly re-enable" behavior is
    observable is on the connection object that performed the rebuild, so
    these tests call ``_rebuild_legacy_pk_tables`` directly on a connection
    whose ``foreign_keys`` pragma is set beforehand, exactly as
    ``ResultsStore()`` (via ``_init_db``) does internally.
    """

    @staticmethod
    def _make_legacy_db(db_path: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE run_results (
                    cell_id TEXT PRIMARY KEY,
                    run_name TEXT NOT NULL,
                    harness TEXT NOT NULL,
                    model TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    track TEXT NOT NULL,
                    repeat INTEGER NOT NULL,
                    exit_class TEXT NOT NULL,
                    success REAL NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """INSERT INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success, timestamp)
                   VALUES ('cell-z', 'legacy-run', 'opencode', 'm', 'task-1',
                           'swe', 0, 'pass', 1.0, '2024-01-01T00:00:00+00:00')"""
            )
            conn.commit()

    def test_restores_off_when_prior_value_was_off(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        self._make_legacy_db(db_path)

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
            ResultsStore._rebuild_legacy_pk_tables(conn)
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        finally:
            conn.close()

    def test_restores_on_when_prior_value_was_on(self, tmp_path):
        """A blind "off, then unconditionally on" implementation already fails
        ``test_restores_off_when_prior_value_was_off`` (prior OFF, restored
        to ON instead of OFF). This test starts from the non-default ON
        state and guards against the opposite bug: an implementation that
        unconditionally restores to some other constant (e.g. always OFF)
        regardless of what the pragma actually was beforehand.
        """
        db_path = str(tmp_path / "legacy.db")
        self._make_legacy_db(db_path)

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            ResultsStore._rebuild_legacy_pk_tables(conn)
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()

    def test_results_store_construction_restores_pragma_via_public_api(
        self, tmp_path, monkeypatch
    ):
        """The restoration must happen when going through ``ResultsStore()`` itself.

        The two tests above call ``_rebuild_legacy_pk_tables`` directly,
        which proves the toggle-and-restore logic is correct in isolation,
        but not that ``_init_db`` actually wires it up on the real
        construction path. ``PRAGMA foreign_keys`` is per-connection and
        ``_init_db`` closes its connection before ``ResultsStore()``
        returns, so the value can't be read back from a fresh connection
        afterwards — a brand-new connection always reports SQLite's own
        default regardless of what happened on the old one, which is
        exactly why an earlier version of this test (comparing two fresh
        connections' defaults) passed even with the whole migration deleted.

        Instead, this spies on every ``PRAGMA foreign_keys = ...`` statement
        that a real ``ResultsStore(db_path)`` construction executes (via a
        thin wrapper around the connection ``sqlite3.connect`` returns, since
        ``sqlite3.Connection`` is a C type and can't be monkeypatched
        directly) and asserts the *last* one restores the pragma to the
        value it started at, not some other constant.
        """
        db_path = str(tmp_path / "legacy.db")
        self._make_legacy_db(db_path)

        pragma_calls: list[str] = []
        real_connect = sqlite3.connect

        class _SpyConn:
            def __init__(self, conn: sqlite3.Connection) -> None:
                object.__setattr__(self, "_conn", conn)

            def execute(self, sql: str, *args: object, **kwargs: object) -> object:
                stripped = sql.strip() if isinstance(sql, str) else sql
                if (
                    isinstance(stripped, str)
                    and stripped.upper().startswith("PRAGMA FOREIGN_KEYS")
                    and "=" in stripped
                ):
                    pragma_calls.append(stripped)
                return self._conn.execute(sql, *args, **kwargs)  # type: ignore[attr-defined]

            def __getattr__(self, name: str) -> object:
                return getattr(self._conn, name)  # type: ignore[attr-defined]

        def spying_connect(*args: object, **kwargs: object) -> _SpyConn:
            return _SpyConn(real_connect(*args, **kwargs))  # type: ignore[arg-type]

        monkeypatch.setattr(sqlite3, "connect", spying_connect)

        ResultsStore(db_path)

        assert pragma_calls, "expected ResultsStore() to toggle PRAGMA foreign_keys at all"
        assert pragma_calls[0] == "PRAGMA foreign_keys = OFF"
        assert pragma_calls[-1] == "PRAGMA foreign_keys = 0", (
            f"pragma not restored to its original (0/OFF) value: {pragma_calls}"
        )


class TestBillableCost:
    def test_excludes_subscription_includes_platform_and_null(self, store):
        platform_cell = _make_cell("run-a", cost_mode=CostMode.PLATFORM)
        store.save_result(
            cell=platform_cell,
            exit_class="pass",
            success=1.0,
            total_cost=1.0,
        )

        subscription_cell = platform_cell.model_copy(
            update={
                "model": platform_cell.model.model_copy(
                    update={"name": "sub-model", "cost_mode": CostMode.SUBSCRIPTION}
                )
            }
        )
        store.save_result(
            cell=subscription_cell,
            exit_class="pass",
            success=1.0,
            total_cost=5.0,
        )

        # Simulate a legacy row with a NULL cost_mode (pre-migration data).
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                """INSERT INTO run_results
                   (cell_id, run_name, harness, model, task_id, track, repeat,
                    exit_class, success, total_cost, timestamp, cost_mode)
                   VALUES ('legacy-cell', 'run-a', 'opencode', 'm', 'task-1',
                           'swe', 1, 'pass', 1.0, 3.0, '2024-01-01T00:00:00+00:00', NULL)"""
            )
            conn.commit()

        assert store.get_total_cost("run-a") == pytest.approx(1.0 + 5.0 + 3.0)
        # Billable excludes the subscription row but includes platform + NULL.
        assert store.get_billable_cost("run-a") == pytest.approx(1.0 + 3.0)
