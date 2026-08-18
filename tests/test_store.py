"""Tests for gtp.store, the single place CLI and GUI both save through."""

from pathlib import Path

from gtp.db import init_db
from gtp.models import BalanceSnapshot, DailyReading
from gtp.store import save_balance_snapshot, save_daily_reading


def test_save_daily_reading_writes_expected_row(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    reading = DailyReading(
        date="2026-08-14", fit0101_reading=40.0, fit0101_error=0.5,
        fit0501=39.0, op_fraction=1.0, bore_status="on", comment="normal day",
        is_estimated=0, estimate_reason=None,
    )
    save_daily_reading(conn, reading, override_reason=None)

    row = conn.execute(
        "SELECT date, fit0101_reading, fit0101_error, fit0501, op_fraction, "
        "bore_status, comment, is_estimated, estimate_reason, override_reason "
        "FROM daily_reading WHERE date = '2026-08-14'"
    ).fetchone()
    assert row == ("2026-08-14", 40.0, 0.5, 39.0, 1.0, "on", "normal day", 0, None, None)


def test_save_daily_reading_sets_entered_at(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    reading = DailyReading(date="2026-08-14", fit0101_reading=40.0, fit0501=39.0, op_fraction=1.0)
    save_daily_reading(conn, reading, override_reason=None)

    entered_at = conn.execute(
        "SELECT entered_at FROM daily_reading WHERE date = '2026-08-14'"
    ).fetchone()[0]
    assert entered_at is not None


def test_save_daily_reading_stores_override_reason(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    reading = DailyReading(date="2026-08-14", fit0101_reading=-5.0, fit0501=39.0, op_fraction=1.0)
    save_daily_reading(conn, reading, override_reason="confirmed with site visit")

    override_reason = conn.execute(
        "SELECT override_reason FROM daily_reading WHERE date = '2026-08-14'"
    ).fetchone()[0]
    assert override_reason == "confirmed with site visit"


def test_save_daily_reading_commits(tmp_path: Path):
    """Both the CLI and the GUI open one connection and rely on the save
    itself committing -- no separate conn.commit() call at the caller.
    """
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    reading = DailyReading(date="2026-08-14", fit0101_reading=40.0, fit0501=39.0, op_fraction=1.0)
    save_daily_reading(conn, reading, override_reason=None)
    conn.close()

    reopened = init_db(db_path)
    row = reopened.execute(
        "SELECT date FROM daily_reading WHERE date = '2026-08-14'"
    ).fetchone()
    assert row == ("2026-08-14",)


def test_save_balance_snapshot_writes_expected_row(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    snapshot = BalanceSnapshot(
        taken_at="2026-08-14T09:00:00", t02=100.0, t31=50.0, t32=50.0, t5=20.0,
        f4s=0.0, mains_meter=1700.5, other_adjustments=0.0, other_note=None, note="weekly check",
    )
    save_balance_snapshot(conn, snapshot, override_reason=None)

    row = conn.execute(
        "SELECT taken_at, t02, t31, t32, t5, f4s, mains_meter, "
        "other_adjustments, other_note, note, override_reason "
        "FROM balance_snapshot WHERE taken_at = '2026-08-14T09:00:00'"
    ).fetchone()
    assert row == (
        "2026-08-14T09:00:00", 100.0, 50.0, 50.0, 20.0, 0.0, 1700.5, 0.0, None, "weekly check", None,
    )


def test_save_balance_snapshot_stores_override_reason(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    snapshot = BalanceSnapshot(
        taken_at="2026-08-14T09:00:00", t02=100.0, t31=50.0, t32=50.0, t5=20.0, mains_meter=1700.5,
    )
    save_balance_snapshot(conn, snapshot, override_reason="mains meter re-read manually")

    override_reason = conn.execute(
        "SELECT override_reason FROM balance_snapshot WHERE taken_at = '2026-08-14T09:00:00'"
    ).fetchone()[0]
    assert override_reason == "mains meter re-read manually"
