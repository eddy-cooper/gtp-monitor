"""CLI-level tests: the confirmation step, BLOCK overriding, gtp check,
and gtp override. Uses typer's own CliRunner (ships with typer, no new
dependency) rather than calling the underlying functions directly, since
these tests are specifically about the command-line behaviour.

The database-backed tests run against the synthetic demo dataset
(demo/Demo Operational Record.xlsx + tools/setup_demo.py).
"""

import sqlite3
import sys
from pathlib import Path

from typer.testing import CliRunner

from gtp.cli import app
from gtp.db import init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import setup_demo  # noqa: E402

DEMO_WORKBOOK = (
    Path(__file__).resolve().parent.parent / "demo" / "Demo Operational Record.xlsx"
)

runner = CliRunner()


def _db_args(db_path: Path) -> list[str]:
    return ["--db-path", str(db_path)]


def test_add_blocked_entry_refused_without_override(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, [
        "add", "--date", "2026-08-01", "--fit0101-reading", "60.0",
        "--fit0501", "59.0", "--op-fraction", "1.0", *_db_args(db_path),
    ])
    assert result.exit_code == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_reading WHERE date = '2026-08-01'"
    ).fetchone()[0] == 0


def test_add_blocked_entry_saved_with_override(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, [
        "add", "--date", "2026-08-01", "--fit0101-reading", "60.0",
        "--fit0501", "59.0", "--op-fraction", "1.0",
        "--override", "confirmed with site visit", "--yes", *_db_args(db_path),
    ])
    assert result.exit_code == 0
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT fit0101_reading, override_reason FROM daily_reading WHERE date = '2026-08-01'"
    ).fetchone()
    assert row == (60.0, "confirmed with site visit")


def test_add_warn_only_entry_saves_with_no_override_reason(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    # fit0101_reading below typical_daily_min_kl while op_fraction=1.0 -> V4 WARN only
    result = runner.invoke(app, [
        "add", "--date", "2026-08-01", "--fit0101-reading", "20.0",
        "--fit0501", "19.5", "--op-fraction", "1.0", "--yes", *_db_args(db_path),
    ])
    assert result.exit_code == 0
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT override_reason FROM daily_reading WHERE date = '2026-08-01'"
    ).fetchone()
    assert row == (None,)


def test_add_confirmation_prompt_declined_saves_nothing(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, [
        "add", "--date", "2026-08-01", "--fit0101-reading", "40.0",
        "--fit0501", "39.5", "--op-fraction", "1.0", *_db_args(db_path),
    ], input="n\n")
    assert result.exit_code == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_reading WHERE date = '2026-08-01'"
    ).fetchone()[0] == 0


def test_add_confirmation_prompt_accepted_saves(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, [
        "add", "--date", "2026-08-01", "--fit0101-reading", "40.0",
        "--fit0501", "39.5", "--op-fraction", "1.0", *_db_args(db_path),
    ], input="y\n")
    assert result.exit_code == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_reading WHERE date = '2026-08-01'"
    ).fetchone()[0] == 1


def test_add_yes_flag_skips_prompt_entirely(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, [
        "add", "--date", "2026-08-01", "--fit0101-reading", "40.0",
        "--fit0501", "39.5", "--op-fraction", "1.0", "--yes", *_db_args(db_path),
    ])
    assert result.exit_code == 0
    assert "Save this entry?" not in result.output
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_reading WHERE date = '2026-08-01'"
    ).fetchone()[0] == 1


def test_snapshot_v8_decrease_refused_without_override(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO balance_snapshot (taken_at, t02, t31, t32, t5, mains_meter) "
        "VALUES ('2026-07-30T00:00:00', 1, 1, 1, 1, 1690.5)"
    )
    conn.commit()
    result = runner.invoke(app, [
        "snapshot", "--taken-at", "2026-08-10T09:00:00", "--t02", "1", "--t31", "1",
        "--t32", "1", "--t5", "1", "--mains-meter", "1680.0", "--yes", *_db_args(db_path),
    ])
    assert result.exit_code == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM balance_snapshot WHERE taken_at = '2026-08-10T09:00:00'"
    ).fetchone()[0] == 0


def test_override_command_sets_reason_on_existing_row(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-01-31', 40.0, 39.5, 1.0, 0, 'now')"
    )
    conn.commit()
    result = runner.invoke(app, [
        "override", "--date", "2026-01-31", "--reason", "explained by site visit",
        *_db_args(db_path),
    ])
    assert result.exit_code == 0
    row = conn.execute(
        "SELECT override_reason FROM daily_reading WHERE date = '2026-01-31'"
    ).fetchone()
    assert row == ("explained by site visit",)


def test_override_command_errors_on_missing_row(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, [
        "override", "--date", "2026-01-31", "--reason", "x", *_db_args(db_path),
    ])
    assert result.exit_code == 1


def test_override_command_requires_exactly_one_locator(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, ["override", "--reason", "x", *_db_args(db_path)])
    assert result.exit_code == 1


def _demo_db(tmp_path: Path) -> Path:
    """The full demo database: imported workbook, all balances computed,
    and the staged mis-keyed mains snapshot left unresolved."""
    db_path = tmp_path / "gtp.db"
    setup_demo.build_demo_db(db_path, DEMO_WORKBOOK)
    return db_path


def test_check_command_smoke_test(tmp_path: Path):
    db_path = _demo_db(tmp_path)
    result = runner.invoke(app, ["check", *_db_args(db_path)])
    assert result.exit_code == 0
    # the staged mis-keyed mains snapshot surfaces as an unresolved V9 BLOCK
    assert "V9" in result.output
    assert "2026-08-16" in result.output


def test_check_since_hides_older_findings(tmp_path: Path):
    db_path = _demo_db(tmp_path)
    result = runner.invoke(app, ["check", "--since", "2026-03-01", *_db_args(db_path)])
    assert result.exit_code == 0
    assert "2026-01-15" not in result.output  # January's V4 warning is filtered out


def test_check_since_still_shows_matching_findings(tmp_path: Path):
    db_path = _demo_db(tmp_path)
    result = runner.invoke(app, ["check", "--since", "2026-01-01", *_db_args(db_path)])
    assert result.exit_code == 0
    assert "2026-01-15" in result.output


def test_check_since_scan_counts_stay_full(tmp_path: Path):
    """The internal scan (needed for correct rolling-history context)
    still covers everything; --since only narrows what gets printed."""
    db_path = _demo_db(tmp_path)
    full = runner.invoke(app, ["check", *_db_args(db_path)])
    narrowed = runner.invoke(app, ["check", "--since", "2026-08-01", *_db_args(db_path)])
    assert "scanned" in full.output and "scanned" in narrowed.output
    full_scanned_line = next(l for l in full.output.splitlines() if "scanned" in l)
    narrowed_scanned_line = next(l for l in narrowed.output.splitlines() if "scanned" in l)
    assert full_scanned_line == narrowed_scanned_line


def test_status_no_balance_yet(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    init_db(db_path)
    result = runner.invoke(app, ["status", *_db_args(db_path)])
    assert result.exit_code == 0
    assert "No balance has been computed yet" in result.output


def test_status_demo_data_shows_watch_and_accelerating(tmp_path: Path, monkeypatch):
    # gtp status logs alert-worthy events to disk -- point it at a temp
    # file so this test doesn't write into the project's out/alerts.log.
    monkeypatch.setattr(
        "gtp.report.load_log_path_from_toml", lambda: tmp_path / "alerts.log"
    )
    db_path = _demo_db(tmp_path)
    result = runner.invoke(app, ["status", *_db_args(db_path)])
    assert result.exit_code == 0
    assert "WATCH" in result.output
    assert "ACTION" not in result.output
    assert "Accelerating:        yes" in result.output


def test_status_logs_alert_to_configured_log_path(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "alerts.log"
    monkeypatch.setattr("gtp.report.load_log_path_from_toml", lambda: log_path)
    db_path = _demo_db(tmp_path)
    result = runner.invoke(app, ["status", *_db_args(db_path)])
    assert result.exit_code == 0
    assert log_path.exists()
    assert "WATCH" in log_path.read_text()


def test_chart_command_creates_file(tmp_path: Path):
    db_path = _demo_db(tmp_path)
    out_path = tmp_path / "trend.png"
    result = runner.invoke(app, ["chart", "--out", str(out_path), *_db_args(db_path)])
    assert result.exit_code == 0
    assert out_path.exists()
    assert out_path.stat().st_size > 0
