"""Schema creation tests for gtp.db."""

import sqlite3
from pathlib import Path

import pytest

from gtp.db import init_db

EXPECTED_TABLES = {
    "daily_reading",
    "balance_snapshot",
    "balance_result",
    "service_event",
    "legacy_balance_note",
    "config_history",
}

EXPECTED_COLUMNS = {
    "daily_reading": {
        "date", "fit0101_reading", "fit0101_error", "fit0501", "op_fraction",
        "bore_status", "comment", "is_estimated", "estimate_reason", "entered_at",
        "override_reason",
    },
    "balance_snapshot": {
        "id", "taken_at", "t02", "t31", "t32", "t5", "f4s", "mains_meter",
        "other_adjustments", "other_note", "note", "override_reason",
    },
    "balance_result": {
        "id", "period_start_snapshot_id", "period_end_snapshot_id",
        "period_start", "period_end", "total_in", "total_out",
        "start_volume", "end_volume", "mains_used", "chemicals_used",
        "other_adjustments", "discrepancy_subtotal", "fit0101_under_kl",
        "fit0101_under_pct", "batch_volume_kl", "chem_litres_per_batch",
        "computed_at", "contains_estimates",
    },
    "service_event": {"id", "date", "item", "note", "hours_at_service", "interval_hours"},
    "legacy_balance_note": {"id", "sheet", "row", "label", "kl", "pct"},
    "config_history": {
        "id", "effective_from", "batch_volume_kl", "chem_base_litres_per_batch",
        "chem_acid_factor", "chem_ferrous_factor", "chem_h2o2_factor",
        "chem_caustic_factor", "mains_used_reported", "source",
    },
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r[0] for r in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_init_db_creates_all_tables(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    assert EXPECTED_TABLES.issubset(_table_names(conn))


def test_init_db_creates_parent_directory(tmp_path: Path):
    db_path = tmp_path / "nested" / "dir" / "gtp.db"
    init_db(db_path)
    assert db_path.exists()


def test_init_db_columns_match_spec(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    for table, expected_cols in EXPECTED_COLUMNS.items():
        assert _column_names(conn, table) == expected_cols, table


def test_init_db_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path).close()
    init_db(db_path).close()
    conn = init_db(db_path)
    assert EXPECTED_TABLES.issubset(_table_names(conn))


def test_daily_reading_date_is_primary_key(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-08-01', 10.0, 9.0, 1.0, 0, 'now')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
            "is_estimated, entered_at) VALUES ('2026-08-01', 11.0, 9.5, 1.0, 0, 'now')"
        )


def test_balance_snapshot_taken_at_is_unique(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO balance_snapshot (taken_at, t02, t31, t32, t5, mains_meter) "
        "VALUES ('2026-08-01T00:00:00', 1, 1, 1, 1, 100)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO balance_snapshot (taken_at, t02, t31, t32, t5, mains_meter) "
            "VALUES ('2026-08-01T00:00:00', 2, 2, 2, 2, 200)"
        )


def test_balance_result_period_is_unique(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end) VALUES ('2026-07-01', '2026-07-31')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO balance_result (period_start, period_end) VALUES ('2026-07-01', '2026-07-31')"
        )


def test_config_history_effective_from_is_unique(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO config_history (effective_from, batch_volume_kl) VALUES ('2026-07-01', 4.9)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO config_history (effective_from, batch_volume_kl) VALUES ('2026-07-01', 5.0)"
        )


def test_init_db_migrates_old_empty_balance_result(tmp_path: Path):
    """Phase 1 databases have balance_result without the new uniqueness
    rule. init_db should transparently add it, since the table was always
    left empty until Phase 2.
    """
    db_path = tmp_path / "gtp.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE balance_result (id INTEGER PRIMARY KEY, period_start TEXT, period_end TEXT)")
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end) VALUES ('2026-07-01', '2026-07-31')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO balance_result (period_start, period_end) VALUES ('2026-07-01', '2026-07-31')"
        )


def test_init_db_migrates_override_reason_column_without_data_loss(tmp_path: Path):
    """daily_reading and balance_snapshot hold real historical data, unlike
    balance_result/config_history. This migration must ADD the column,
    never drop or rebuild either table -- proven here by inserting a row
    on the old schema, migrating, and confirming that exact row survives.
    """
    db_path = tmp_path / "gtp.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE daily_reading (date TEXT PRIMARY KEY, fit0101_reading REAL, "
        "fit0101_error REAL DEFAULT 0, fit0501 REAL, op_fraction REAL, bore_status TEXT, "
        "comment TEXT, is_estimated INTEGER NOT NULL DEFAULT 0, estimate_reason TEXT, entered_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE balance_snapshot (id INTEGER PRIMARY KEY, taken_at TEXT NOT NULL UNIQUE, "
        "t02 REAL, t31 REAL, t32 REAL, t5 REAL, f4s REAL DEFAULT 0, mains_meter REAL, "
        "other_adjustments REAL DEFAULT 0, other_note TEXT, note TEXT)"
    )
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-01-31', 40.0, 39.5, 1.0, 0, 'now')"
    )
    conn.execute(
        "INSERT INTO balance_snapshot (taken_at, t02, t31, t32, t5, mains_meter) "
        "VALUES ('2026-01-31T00:00:00', 1, 1, 1, 1, 1665.5)"
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    reading = conn.execute(
        "SELECT date, fit0101_reading, override_reason FROM daily_reading WHERE date = '2026-01-31'"
    ).fetchone()
    assert reading == ("2026-01-31", 40.0, None)
    snapshot = conn.execute(
        "SELECT taken_at, mains_meter, override_reason FROM balance_snapshot "
        "WHERE taken_at = '2026-01-31T00:00:00'"
    ).fetchone()
    assert snapshot == ("2026-01-31T00:00:00", 1665.5, None)

    conn.execute(
        "UPDATE daily_reading SET override_reason = 'test' WHERE date = '2026-01-31'"
    )
    conn.commit()  # proves the column is genuinely writable, not just present


def test_init_db_migrates_contains_estimates_with_correct_backfill(tmp_path: Path):
    """balance_result holds real calculated history, so this migration
    must ADD the column (never drop/rebuild), and must correctly backfill
    existing rows from daily_reading.is_estimated -- not just default
    everything to 0, which would be wrong for a period that genuinely
    contains estimated days.
    """
    db_path = tmp_path / "gtp.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE balance_result (id INTEGER PRIMARY KEY, period_start TEXT, "
        "period_end TEXT, fit0101_under_kl REAL, UNIQUE (period_start, period_end))"
    )
    conn.execute(
        "CREATE TABLE daily_reading (date TEXT PRIMARY KEY, fit0101_reading REAL, "
        "fit0101_error REAL DEFAULT 0, fit0501 REAL, op_fraction REAL, bore_status TEXT, "
        "comment TEXT, is_estimated INTEGER NOT NULL DEFAULT 0, estimate_reason TEXT, entered_at TEXT)"
    )
    # July: one estimated day inside the period -> should backfill to 1
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, fit0101_under_kl) "
        "VALUES ('2026-07-01', '2026-07-31', 6.224)"
    )
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-07-30', 39.0, 39.5, 1.0, 1, 'now')"
    )
    # June: no estimated days -> should backfill to 0
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, fit0101_under_kl) "
        "VALUES ('2026-06-01', '2026-06-30', 2.67)"
    )
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-06-15', 40.0, 39.5, 1.0, 0, 'now')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    (count,) = conn.execute("SELECT COUNT(*) FROM balance_result").fetchone()
    assert count == 2  # nothing dropped

    july_flag = conn.execute(
        "SELECT contains_estimates FROM balance_result WHERE period_start = '2026-07-01'"
    ).fetchone()[0]
    june_flag = conn.execute(
        "SELECT contains_estimates FROM balance_result WHERE period_start = '2026-06-01'"
    ).fetchone()[0]
    assert july_flag == 1
    assert june_flag == 0


def test_init_db_migrates_service_event_hours_columns_without_data_loss(tmp_path: Path):
    """service_event holds imported history, so this migration must ADD
    the running-hours columns, never drop or rebuild the table -- proven
    here by inserting a row on the old schema, migrating, and confirming
    that exact row survives with the new columns NULL.
    """
    db_path = tmp_path / "gtp.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE service_event (id INTEGER PRIMARY KEY, date TEXT, item TEXT, note TEXT)"
    )
    conn.execute(
        "INSERT INTO service_event (date, item, note) VALUES ('2026-07-16', 'AE05', '(2)')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    row = conn.execute(
        "SELECT date, item, hours_at_service, interval_hours FROM service_event "
        "WHERE date = '2026-07-16'"
    ).fetchone()
    assert row == ("2026-07-16", "AE05", None, None)

    conn.execute(
        "UPDATE service_event SET hours_at_service = 1850.0, interval_hours = 2000.0 "
        "WHERE date = '2026-07-16'"
    )
    conn.commit()  # proves the columns are genuinely writable, not just present


def test_init_db_refuses_to_migrate_non_empty_old_balance_result(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE balance_result (id INTEGER PRIMARY KEY, period_start TEXT, period_end TEXT)")
    conn.execute("INSERT INTO balance_result (period_start, period_end) VALUES ('2026-07-01', '2026-07-31')")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError):
        init_db(db_path)
