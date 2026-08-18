"""Tests for the validation rules, per docs/DATA_SPEC.md §3.

One test per rule, using small synthetic fixtures, plus acceptance tests
against the imported demo dataset (the staged mains-meter typo and the
PLC-freeze days) to prove the edge cases actually work — not just the
tidy textbook cases.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from gtp.importer import import_workbook
from gtp.models import BalanceSnapshot, DailyReading
from gtp.validate import (
    BLOCK,
    WARN,
    ValidationConfig,
    check_v1_negative_flow,
    check_v2_exceeds_max_daily,
    check_v3_exceeds_typical_daily,
    check_v4_below_typical_min,
    check_v5_op_fraction_mismatch,
    check_v6_possible_plc_freeze,
    check_v7_gap_since_last_entry,
    check_v8_mains_meter_decreased,
    check_v9_mains_meter_spike,
    check_v10_tank_volume_out_of_range,
    check_v11_estimated_without_reason,
    check_v12_daily_imbalance,
    check_v13_future_date,
    load_validation_config_from_toml,
    run_check,
    validate_balance_snapshot,
    validate_daily_reading,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_demo_data as mdd  # noqa: E402
import setup_demo  # noqa: E402

DEMO_WORKBOOK = (
    Path(__file__).resolve().parent.parent / "demo" / "Demo Operational Record.xlsx"
)

CONFIG = ValidationConfig(
    max_daily_kl=58.0,
    typical_daily_min_kl=28.0,
    typical_daily_max_kl=48.0,
    consecutive_freeze_days=3,
    mains_increase_factor=3.0,
    mains_median_window=6,
    daily_imbalance_kl=15.0,
)


def reading(date="2026-08-01", fit0101_reading=40.0, fit0101_error=0.0,
            fit0501=39.5, op_fraction=1.0, is_estimated=0, estimate_reason=None):
    return DailyReading(
        date=date, fit0101_reading=fit0101_reading, fit0101_error=fit0101_error,
        fit0501=fit0501, op_fraction=op_fraction, is_estimated=is_estimated,
        estimate_reason=estimate_reason,
    )


def snapshot(taken_at="2026-08-01T00:00:00", t02=5.0, t31=1.0, t32=5.0, t5=4.0,
             f4s=0.0, mains_meter=1700.0):
    return BalanceSnapshot(
        taken_at=taken_at, t02=t02, t31=t31, t32=t32, t5=t5, f4s=f4s,
        mains_meter=mains_meter,
    )


# --- V1 -------------------------------------------------------------


def test_v1_negative_actual_blocks():
    findings = check_v1_negative_flow(reading(fit0101_reading=5.0, fit0101_error=10.0))
    assert any(f.rule == "V1" and f.level == BLOCK for f in findings)


def test_v1_negative_fit0501_blocks():
    findings = check_v1_negative_flow(reading(fit0501=-1.0))
    assert any(f.rule == "V1" and f.level == BLOCK for f in findings)


def test_v1_normal_values_pass():
    assert check_v1_negative_flow(reading()) == []


# --- V2 / V3 ----------------------------------------------------------


def test_v2_exceeds_max_daily_blocks():
    findings = check_v2_exceeds_max_daily(reading(fit0101_reading=60.0), CONFIG)
    assert any(f.rule == "V2" and f.level == BLOCK for f in findings)


def test_v3_exceeds_typical_but_under_max_warns():
    findings = check_v3_exceeds_typical_daily(reading(fit0101_reading=50.0), CONFIG)
    assert any(f.rule == "V3" and f.level == WARN for f in findings)


def test_v3_does_not_fire_at_or_below_typical_max():
    assert check_v3_exceeds_typical_daily(reading(fit0101_reading=48.0), CONFIG) == []


# --- V4 -----------------------------------------------------------------


def test_v4_below_typical_min_while_fully_operating_warns():
    findings = check_v4_below_typical_min(
        reading(fit0101_reading=20.0, op_fraction=1.0), CONFIG
    )
    assert any(f.rule == "V4" and f.level == WARN for f in findings)


def test_v4_does_not_fire_when_not_fully_operating():
    findings = check_v4_below_typical_min(
        reading(fit0101_reading=20.0, op_fraction=0.5), CONFIG
    )
    assert findings == []


# --- V5 -------------------------------------------------------------------


def test_v5_nonzero_while_offline_warns():
    findings = check_v5_op_fraction_mismatch(
        reading(fit0101_reading=10.0, fit0501=10.0, op_fraction=0.0)
    )
    assert any(f.rule == "V5" and f.level == WARN for f in findings)


def test_v5_zero_while_fully_operating_warns():
    findings = check_v5_op_fraction_mismatch(
        reading(fit0101_reading=0.0, fit0501=0.0, op_fraction=1.0)
    )
    assert any(f.rule == "V5" and f.level == WARN for f in findings)


def test_v5_legitimate_zero_month_does_not_warn():
    """An offline month's shape: op_fraction=0, both readings genuinely
    0 -- must not false-positive."""
    findings = check_v5_op_fraction_mismatch(
        reading(fit0101_reading=0.0, fit0501=0.0, op_fraction=0.0)
    )
    assert findings == []


# --- V6 --------------------------------------------------------------------


def test_v6_three_consecutive_identical_days_warns():
    recent = [
        reading(date="2026-07-01", fit0101_reading=39.0, fit0501=39.5),
        reading(date="2026-07-02", fit0101_reading=39.0, fit0501=39.5),
    ]
    candidate = reading(date="2026-07-03", fit0101_reading=39.0, fit0501=39.5)
    findings = check_v6_possible_plc_freeze(candidate, recent, CONFIG)
    assert any(f.rule == "V6" and f.level == WARN for f in findings)


def test_v6_two_consecutive_identical_days_does_not_fire():
    """Two identical days is one short of the 3-day threshold, so V6,
    exactly as specified, legitimately stays quiet -- gtp check's
    separate estimated-data reporting is what catches a short freeze."""
    recent = [reading(date="2026-07-30", fit0101_reading=39.0, fit0501=39.5)]
    candidate = reading(date="2026-07-31", fit0101_reading=39.0, fit0501=39.5)
    findings = check_v6_possible_plc_freeze(candidate, recent, CONFIG)
    assert findings == []


def test_v6_does_not_fire_when_values_differ():
    recent = [
        reading(date="2026-07-01", fit0101_reading=39.0, fit0501=39.5),
        reading(date="2026-07-02", fit0101_reading=39.0, fit0501=39.5),
    ]
    candidate = reading(date="2026-07-03", fit0101_reading=38.5, fit0501=38.5)
    assert check_v6_possible_plc_freeze(candidate, recent, CONFIG) == []


# --- V7 ------------------------------------------------------------------


def test_v7_gap_lists_missing_dates():
    previous = reading(date="2026-07-01")
    candidate = reading(date="2026-07-05")
    findings = check_v7_gap_since_last_entry(candidate, previous)
    assert len(findings) == 1
    assert findings[0].rule == "V7" and findings[0].level == WARN
    assert "2026-07-02" in findings[0].message
    assert "2026-07-03" in findings[0].message
    assert "2026-07-04" in findings[0].message


def test_v7_no_gap_when_previous_day_adjacent():
    previous = reading(date="2026-07-01")
    candidate = reading(date="2026-07-02")
    assert check_v7_gap_since_last_entry(candidate, previous) == []


def test_v7_no_previous_entry_does_not_fire():
    assert check_v7_gap_since_last_entry(reading(), None) == []


def test_v7_month_long_gap_truncates_its_listing():
    """A month-long hole in the record must list the first few missing
    dates and summarize the rest, not print 30 lines."""
    previous = reading(date="2025-11-30")
    candidate = reading(date="2026-01-01")
    findings = check_v7_gap_since_last_entry(candidate, previous)
    assert len(findings) == 1
    assert "and" in findings[0].message and "more" in findings[0].message


# --- V8 --------------------------------------------------------------------


def test_v8_mains_meter_decrease_blocks():
    previous = snapshot(taken_at="2026-06-30T00:00:00", mains_meter=1690.7)
    candidate = snapshot(taken_at="2026-07-30T00:00:00", mains_meter=1686.0)
    findings = check_v8_mains_meter_decreased(candidate, previous)
    assert any(f.rule == "V8" and f.level == BLOCK for f in findings)


def test_v8_increase_does_not_fire():
    previous = snapshot(taken_at="2026-06-30T00:00:00", mains_meter=1690.7)
    candidate = snapshot(taken_at="2026-07-30T00:00:00", mains_meter=1696.2)
    assert check_v8_mains_meter_decreased(candidate, previous) == []


def test_v8_no_previous_snapshot_does_not_fire():
    assert check_v8_mains_meter_decreased(snapshot(), None) == []


# --- V9 ----------------------------------------------------------------


def test_v9_spike_blocks_with_full_history():
    recent = [
        snapshot(taken_at=f"2026-0{m}-28T00:00:00", mains_meter=1600.0 + m * 5.0)
        for m in range(1, 7)
    ]
    candidate = snapshot(taken_at="2026-07-28T00:00:00", mains_meter=recent[-1].mains_meter + 100.0)
    findings = check_v9_mains_meter_spike(candidate, recent, CONFIG)
    assert any(f.rule == "V9" and f.level == BLOCK for f in findings)


def test_v9_spike_blocks_with_only_two_prior_intervals():
    """The rule must work early in the record too: with only 2 prior
    intervals ([5.1, 4.8], median 4.95), a ~100 kL mis-key must still be
    caught (3x median = 14.85 << 102.1)."""
    recent = [
        snapshot(taken_at="2025-10-31T00:00:00", mains_meter=1650.0),
        snapshot(taken_at="2025-11-30T00:00:00", mains_meter=1655.1),
        snapshot(taken_at="2025-12-31T00:00:00", mains_meter=1659.9),
    ]
    candidate = snapshot(taken_at="2026-01-31T00:00:00", mains_meter=1762.0)
    findings = check_v9_mains_meter_spike(candidate, recent, CONFIG)
    assert any(f.rule == "V9" and f.level == BLOCK for f in findings)


def test_v9_normal_increase_does_not_fire():
    recent = [
        snapshot(taken_at="2026-05-31T00:00:00", mains_meter=1686.0),
        snapshot(taken_at="2026-06-30T00:00:00", mains_meter=1690.7),
    ]
    candidate = snapshot(taken_at="2026-07-30T00:00:00", mains_meter=1696.2)
    assert check_v9_mains_meter_spike(candidate, recent, CONFIG) == []


def test_v9_no_prior_history_does_not_fire():
    assert check_v9_mains_meter_spike(snapshot(), [], CONFIG) == []


# --- V10 -------------------------------------------------------------------


def test_v10_negative_tank_volume_blocks():
    findings = check_v10_tank_volume_out_of_range(snapshot(t02=-1.0), CONFIG)
    assert any(f.rule == "V10" and f.level == BLOCK for f in findings)


def test_v10_exceeds_configured_capacity_blocks():
    config = ValidationConfig(**{**CONFIG.__dict__, "tank_capacities_kl": {"t02": 10.0}})
    findings = check_v10_tank_volume_out_of_range(snapshot(t02=15.0), config)
    assert any(f.rule == "V10" and f.level == BLOCK for f in findings)


def test_v10_capacity_check_skipped_when_not_configured():
    """Current production behaviour: no capacity figures exist yet, so
    only the negative-volume half of V10 is active."""
    assert CONFIG.tank_capacities_kl is None
    findings = check_v10_tank_volume_out_of_range(snapshot(t02=999.0), CONFIG)
    assert findings == []


def test_v10_f4s_excluded_from_capacity_check():
    config = ValidationConfig(**{**CONFIG.__dict__, "tank_capacities_kl": {"t02": 10.0}})
    findings = check_v10_tank_volume_out_of_range(snapshot(f4s=999.0), config)
    assert findings == []


# --- V11 -----------------------------------------------------------------


def test_v11_estimated_without_reason_blocks():
    findings = check_v11_estimated_without_reason(
        reading(is_estimated=1, estimate_reason=None)
    )
    assert any(f.rule == "V11" and f.level == BLOCK for f in findings)


def test_v11_estimated_with_reason_does_not_fire():
    findings = check_v11_estimated_without_reason(
        reading(is_estimated=1, estimate_reason="PLC freeze")
    )
    assert findings == []


# --- V12 -----------------------------------------------------------------


def test_v12_daily_imbalance_over_threshold_warns():
    findings = check_v12_daily_imbalance(
        reading(fit0101_reading=40.0, fit0501=60.0), CONFIG
    )
    assert any(f.rule == "V12" and f.level == WARN for f in findings)


def test_v12_small_imbalance_does_not_fire():
    findings = check_v12_daily_imbalance(
        reading(fit0101_reading=40.0, fit0501=41.0), CONFIG
    )
    assert findings == []


# --- V13 -----------------------------------------------------------------


def test_v13_future_date_blocks():
    findings = check_v13_future_date("daily_reading", "2026-09-01", today="2026-08-13")
    assert any(f.rule == "V13" and f.level == BLOCK for f in findings)


def test_v13_today_does_not_fire():
    assert check_v13_future_date("daily_reading", "2026-08-13", today="2026-08-13") == []


def test_v13_past_date_does_not_fire():
    assert check_v13_future_date("daily_reading", "2026-01-01", today="2026-08-13") == []


def test_v13_snapshot_taken_today_does_not_fire():
    """A balance_snapshot's subject is a full ISO datetime, not a plain
    date. A naive string comparison against a plain 'today' date would
    make a snapshot taken today (e.g. '2026-08-13T09:00:00') falsely
    look 'later' than 'today' ('2026-08-13'), since the longer string
    sorts after its own prefix. Must compare date portions only."""
    findings = check_v13_future_date(
        "balance_snapshot", "2026-08-13T09:00:00", today="2026-08-13"
    )
    assert findings == []


# --- aggregators -----------------------------------------------------------


def test_validate_daily_reading_combines_multiple_findings():
    findings = validate_daily_reading(
        reading(date="2026-08-13", fit0101_reading=60.0, fit0501=59.5),
        recent_readings=[],
        config=CONFIG,
        today="2026-08-13",
    )
    rules = {f.rule for f in findings}
    assert "V2" in rules  # over max daily


def test_validate_balance_snapshot_combines_multiple_findings():
    findings = validate_balance_snapshot(
        snapshot(taken_at="2026-08-13T00:00:00", t02=-1.0),
        recent_snapshots=[],
        config=CONFIG,
        today="2026-08-13",
    )
    rules = {f.rule for f in findings}
    assert "V10" in rules


# --- config loader -----------------------------------------------------------


def test_load_validation_config_from_toml_matches_shipped_config():
    config = load_validation_config_from_toml()
    assert config.max_daily_kl == 58.0
    assert config.typical_daily_min_kl == 28.0
    assert config.typical_daily_max_kl == 48.0
    assert config.consecutive_freeze_days == 3
    assert config.mains_increase_factor == 3.0
    assert config.mains_median_window == 6
    assert config.daily_imbalance_kl == 15.0
    assert config.tank_capacities_kl is None


# --- gtp check acceptance tests, against the imported demo data --------------


@pytest.fixture(scope="module")
def imported_db_path(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("validate") / "gtp.db"
    setup_demo.build_demo_db(db_path, DEMO_WORKBOOK)
    return db_path


@pytest.fixture()
def conn(imported_db_path, tmp_path):
    working_copy = tmp_path / "gtp.db"
    shutil.copy(imported_db_path, working_copy)
    c = sqlite3.connect(working_copy)
    yield c
    c.close()


def test_check_flags_the_staged_mains_typo(conn):
    report = run_check(conn, config=CONFIG, today="2026-08-18")
    assert any(
        f.rule == "V9" and f.subject == mdd.TYPO_SNAPSHOT["taken_at"]
        for f in report.unresolved_blocks
    )


def test_check_reports_plc_freeze_days_as_estimated(conn):
    report = run_check(conn, config=CONFIG, today="2026-08-18")
    dates = {d for d, _ in report.estimated_entries}
    assert {"2026-05-12", "2026-05-13", "2026-08-09"}.issubset(dates)


def test_check_skips_blank_placeholder_rows(conn):
    """Aug 15-31 in the demo data are blank template rows (day numbers
    with no reading entered yet). They must not spuriously trigger V13
    (future date)."""
    report = run_check(conn, config=CONFIG, today="2026-08-18")
    assert not any(
        f.rule == "V13" and f.subject > "2026-08-18" for f in report.warnings
    )
    assert not any(
        f.rule == "V13" and f.subject > "2026-08-18" for f in report.unresolved_blocks
    )


def test_check_run_is_read_only(conn):
    before = conn.execute("SELECT COUNT(*) FROM daily_reading").fetchone()[0]
    run_check(conn, config=CONFIG, today="2026-08-13")
    after = conn.execute("SELECT COUNT(*) FROM daily_reading").fetchone()[0]
    assert before == after
