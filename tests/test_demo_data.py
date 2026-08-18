"""Tests for the synthetic demo dataset (tools/make_demo_data.py +
tools/setup_demo.py): the generated workbook must flow through the real
import -> balance -> alert pipeline and land exactly on the figures the
generator recorded, and every story beat the showcase README relies on
must actually be present.

Validation/alert configs are passed explicitly (the public demo values)
rather than read from config.toml, so these tests pass identically in
the private repo and the public showcase repo.
"""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_demo_data as mdd  # noqa: E402
import setup_demo  # noqa: E402

from gtp.alerts import AlertConfig, evaluate_alert, trailing_periods_pct
from gtp.chart import fetch_trend_points
from gtp.validate import ValidationConfig, run_check

DEMO_VALIDATION = ValidationConfig(
    max_daily_kl=58.0, typical_daily_min_kl=28.0, typical_daily_max_kl=48.0,
    consecutive_freeze_days=3, mains_increase_factor=3.0, mains_median_window=6,
    daily_imbalance_kl=15.0, tank_capacities_kl=None,
)
DEMO_ALERTS = AlertConfig(
    action_pct=1.0, watch_pct=0.5, accelerating_factor=2.0,
    accelerating_window=3, investigate_pct=-0.5,
)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    base = tmp_path_factory.mktemp("demo_data")
    workbook = base / "Demo Operational Record.xlsx"
    db_path = base / "gtp.db"

    months = mdd.build_months()
    mdd.check_story(months)
    mdd.write_workbook(months, workbook)
    figures = mdd.expected_figures(months)

    summary = setup_demo.build_demo_db(db_path, workbook)
    conn = sqlite3.connect(db_path)
    yield SimpleNamespace(conn=conn, figures=figures, summary=summary, workbook=workbook)
    conn.close()


def test_generator_is_deterministic():
    first = mdd.expected_figures(mdd.build_months())
    second = mdd.expected_figures(mdd.build_months())
    assert first == second


def test_import_summary(demo):
    assert demo.summary.sheets_imported == 11
    assert demo.summary.days_imported == demo.figures["days_total"]
    # exactly one deliberate unparsed service cell ("OFFLINE" in Feb)
    assert len(demo.summary.warnings) == 1
    assert "OFFLINE" in demo.summary.warnings[0]


def test_snapshots_share_month_boundaries(demo):
    (count,) = demo.conn.execute("SELECT COUNT(*) FROM balance_snapshot").fetchone()
    # 12 boundary snapshots (11 sheets sharing boundaries) + the typo entry
    assert count == demo.figures["distinct_snapshots"] + 1


def test_balances_match_expected_figures(demo):
    for key, expected in demo.figures["periods"].items():
        row = demo.conn.execute(
            "SELECT total_in, total_out, mains_used, chemicals_used, "
            "other_adjustments, fit0101_under_kl, fit0101_under_pct, contains_estimates "
            "FROM balance_result WHERE period_start = ? AND period_end = ?",
            (expected["period_start"], expected["period_end"]),
        ).fetchone()
        assert row is not None, f"no balance_result for {key}"
        total_in, total_out, mains, chems, other, under_kl, under_pct, has_est = row
        assert total_in == pytest.approx(expected["total_in"], rel=1e-9), key
        assert total_out == pytest.approx(expected["total_out"], rel=1e-9), key
        assert mains == pytest.approx(expected["mains_used"], abs=1e-9), key
        assert chems == pytest.approx(expected["chemicals_used"], rel=1e-9), key
        assert other == pytest.approx(expected["other_adjustments"], abs=1e-9), key
        assert under_kl == pytest.approx(expected["under_kl"], abs=1e-9), key
        if expected["under_pct"] is None:
            assert under_pct is None, key
        else:
            assert under_pct == pytest.approx(expected["under_pct"], rel=1e-9), key
        assert bool(has_est) == expected["contains_estimates"], key


def test_offline_month_has_no_percentage(demo):
    (pct,) = demo.conn.execute(
        "SELECT fit0101_under_pct FROM balance_result WHERE period_start = '2026-02-01'"
    ).fetchone()
    assert pct is None


def test_estimated_days_flagged_by_import(demo):
    rows = demo.conn.execute(
        "SELECT date, estimate_reason FROM daily_reading WHERE is_estimated = 1 ORDER BY date"
    ).fetchall()
    assert [r[0] for r in rows] == demo.figures["estimated_dates"]
    assert all("PLC freeze" in r[1] for r in rows)


def test_config_change_recorded_in_history(demo):
    rows = demo.conn.execute(
        "SELECT DISTINCT batch_volume_kl FROM config_history ORDER BY effective_from"
    ).fetchall()
    assert {r[0] for r in rows} == {
        mdd.CONFIG_A["batch_volume_kl"], mdd.CONFIG_B["batch_volume_kl"]
    }


def test_latest_period_is_watch_and_accelerating(demo):
    (current_pct,) = demo.conn.execute(
        "SELECT fit0101_under_pct FROM balance_result ORDER BY period_start DESC LIMIT 1"
    ).fetchone()
    previous = trailing_periods_pct(demo.conn, "2026-08-01", DEMO_ALERTS.accelerating_window)
    evaluation = evaluate_alert(current_pct, previous, DEMO_ALERTS)
    assert evaluation.level == "WATCH"
    assert evaluation.accelerating is True


def test_trend_points_tell_the_story(demo):
    points = fetch_trend_points(demo.conn)
    assert len(points) == 11
    labels = [p.label for p in points]
    assert labels[0] == "Oct 2025"
    assert labels[-1] == "Aug 2026 (1–14)"  # partial month gets a day-range label
    by_label = {p.label: p for p in points}
    assert by_label["Feb 2026"].under_pct is None  # the chart gap
    assert by_label["May 2026"].contains_estimates  # hollow marker
    assert by_label["Aug 2026 (1–14)"].contains_estimates
    # exactly the two watch-threshold crossings the story promises
    over_watch = [p.label for p in points if p.under_pct is not None and p.under_pct * 100 > 0.5]
    assert over_watch == ["Jul 2026", "Aug 2026 (1–14)"]


def test_check_finds_exactly_the_staged_block(demo):
    report = run_check(demo.conn, config=DEMO_VALIDATION, today="2026-08-18")
    assert len(report.unresolved_blocks) == 1
    finding = report.unresolved_blocks[0]
    assert finding.rule == "V9"
    assert finding.subject == mdd.TYPO_SNAPSHOT["taken_at"]
    assert report.overridden_blocks == []
    # warnings are the deliberate story beats only: the storm day (V3),
    # the low bore-out day (V4), and identical-reading runs (V6) from the
    # offline month and the PLC freeze
    assert {f.rule for f in report.warnings} <= {"V3", "V4", "V6"}
    assert any(f.rule == "V3" and f.subject == "2026-03-20" for f in report.warnings)
    assert any(f.rule == "V4" and f.subject == "2026-01-15" for f in report.warnings)


def test_reimport_is_idempotent(demo):
    from gtp.importer import import_workbook

    before = {
        table: demo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("daily_reading", "balance_snapshot", "service_event", "config_history")
    }
    db_path = demo.conn.execute("PRAGMA database_list").fetchone()[2]
    import_workbook(demo.workbook, db_path)
    after = {
        table: demo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("daily_reading", "balance_snapshot", "service_event", "config_history")
    }
    assert before == after


def test_workbook_metadata_is_clean(demo):
    import openpyxl

    wb = openpyxl.load_workbook(demo.workbook)
    assert wb.properties.creator == "GTP demo data generator"
    assert wb.properties.lastModifiedBy == "GTP demo data generator"
