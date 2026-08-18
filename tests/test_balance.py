"""Tests for the balance engine, per docs/DATA_SPEC.md §2.

The worked example is written with hand-computed figures; the
database-backed tests reproduce the synthetic demo dataset's recorded
figures (demo/expected_figures.json) through the full import -> balance
pipeline.
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from gtp.balance import (
    BalanceConfig,
    calculate_balance,
    compute_period_balance,
    find_bounding_snapshot,
    get_daily_readings,
    resolve_config,
)
from gtp.importer import import_workbook
from gtp.models import BalanceSnapshot, DailyReading

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_WORKBOOK = REPO_ROOT / "demo" / "Demo Operational Record.xlsx"
EXPECTED_FIGURES = json.loads(
    (REPO_ROOT / "demo" / "expected_figures.json").read_text(encoding="utf-8")
)


def test_worked_example_by_hand():
    """Every figure below is computed by hand from the formula in
    docs/DATA_SPEC.md §2, so the code is checked against the spec rather
    than against itself.

    start volume 3.00+2.50+2.00+1.50 = 9.00; end 3.40+2.80+2.20+3.60 = 12.00
    total_in 1000.0-2.0 = 998.0; total_out 1004.0
    expected end = 9.00 + (998.0-1004.0) = 3.00 -> discrepancy 9.00
    mains 505.0-500.0 = 5.00
    chem litres/batch = 3.0x(1+1+1+1) = 12.0
    chemicals = (1004.0/5.0) x 12.0 / 1000 = 2.4096
    under = 9.00 - 5.00 - 2.4096 - 0 = 1.5904 ; pct = 1.5904/1004.0
    """
    start = BalanceSnapshot(taken_at="2026-06-30T00:00:00", t02=3.00, t31=2.50,
                            t32=2.00, t5=1.50, f4s=0.0, mains_meter=500.0)
    end = BalanceSnapshot(taken_at="2026-07-31T00:00:00", t02=3.40, t31=2.80,
                          t32=2.20, t5=3.60, f4s=0.0, mains_meter=505.0,
                          other_adjustments=0.0)
    readings = [DailyReading(date="2026-07-01", fit0101_reading=1000.0,
                             fit0101_error=2.0, fit0501=1004.0)]
    config = BalanceConfig(
        batch_volume_kl=5.0, chem_base_litres_per_batch=3.0,
        chem_acid_factor=1.0, chem_ferrous_factor=1.0,
        chem_h2o2_factor=1.0, chem_caustic_factor=1.0,
    )

    result = calculate_balance(start, end, readings, config)

    assert result.total_in == pytest.approx(998.0)
    assert result.total_out == pytest.approx(1004.0)
    assert result.start_volume == pytest.approx(9.00)
    assert result.end_volume == pytest.approx(12.00)
    assert result.discrepancy_subtotal == pytest.approx(9.00)
    assert result.mains_used == pytest.approx(5.00)
    assert result.chemicals_used == pytest.approx(2.4096)
    assert result.fit0101_under_kl == pytest.approx(1.5904)
    assert result.fit0101_under_pct == pytest.approx(1.5904 / 1004.0)
    assert result.contains_estimates == 0


def test_contains_estimates_true_when_any_reading_is_estimated():
    start = BalanceSnapshot(taken_at="2026-06-30T00:00:00", t02=3.0, t31=2.5,
                            t32=2.0, t5=1.5, mains_meter=500.0)
    end = BalanceSnapshot(taken_at="2026-07-31T00:00:00", t02=3.4, t31=2.8,
                          t32=2.2, t5=3.6, mains_meter=505.0)
    readings = [
        DailyReading(date="2026-07-01", fit0101_reading=39.0, fit0101_error=0.0,
                     fit0501=39.5, is_estimated=0),
        DailyReading(date="2026-07-30", fit0101_reading=39.0, fit0101_error=0.0,
                     fit0501=39.5, is_estimated=1, estimate_reason="PLC freeze"),
    ]
    config = BalanceConfig(5.0, 3.0, 1.0, 1.0, 1.0, 1.0)

    result = calculate_balance(start, end, readings, config)
    assert result.contains_estimates == 1


def test_zero_total_out_returns_none_percentage():
    start = BalanceSnapshot(taken_at="2026-01-31T00:00:00", t02=0, t31=0, t32=0,
                            t5=0, mains_meter=500.0)
    end = BalanceSnapshot(taken_at="2026-02-28T00:00:00", t02=0, t31=0, t32=0,
                          t5=0, mains_meter=500.0, other_adjustments=0.0)
    readings = [DailyReading(date="2026-02-01", fit0101_reading=0.0,
                             fit0101_error=0.0, fit0501=0.0)]
    config = BalanceConfig(5.0, 3.0, 1.0, 1.0, 1.0, 1.0)

    result = calculate_balance(start, end, readings, config)
    assert result.total_out == 0.0
    assert result.fit0101_under_pct is None


def test_calculate_balance_rejects_missing_reading():
    start = BalanceSnapshot(taken_at="2026-06-30T00:00:00", t02=1, t31=1, t32=1,
                            t5=1, mains_meter=100)
    end = BalanceSnapshot(taken_at="2026-07-30T00:00:00", t02=1, t31=1, t32=1,
                          t5=1, mains_meter=110)
    readings = [DailyReading(date="2026-07-01", fit0101_reading=None,
                             fit0101_error=0.0, fit0501=10.0)]
    config = BalanceConfig(5.0, 3.0, 1.0, 1.0, 1.0, 1.0)

    with pytest.raises(ValueError, match="2026-07-01"):
        calculate_balance(start, end, readings, config)


# --- database-backed tests, against the demo dataset ------------------------


@pytest.fixture(scope="module")
def imported_db_path(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("balance") / "gtp.db"
    import_workbook(DEMO_WORKBOOK, db_path)
    return db_path


@pytest.fixture()
def conn(imported_db_path, tmp_path):
    # Copy the imported database per test so persisted balance_result rows
    # from one test don't leak into another.
    working_copy = tmp_path / "gtp.db"
    shutil.copy(imported_db_path, working_copy)
    c = sqlite3.connect(working_copy)
    yield c
    c.close()


@pytest.mark.parametrize("key,expected", EXPECTED_FIGURES["periods"].items())
def test_compute_period_balance_reproduces_recorded_figures(conn, key, expected):
    result = compute_period_balance(
        conn, expected["period_start"], expected["period_end"], persist=False
    )
    assert result.total_in == pytest.approx(expected["total_in"], rel=1e-9), key
    assert result.total_out == pytest.approx(expected["total_out"], rel=1e-9), key
    assert result.fit0101_under_kl == pytest.approx(expected["under_kl"], abs=1e-9), key
    if expected["under_pct"] is None:
        assert result.fit0101_under_pct is None, key
    else:
        assert result.fit0101_under_pct == pytest.approx(expected["under_pct"], rel=1e-9), key


def test_compute_period_balance_flags_estimated_periods(conn):
    """May contains the PLC-freeze back-filled days; June does not."""
    may = compute_period_balance(conn, "2026-05-01", "2026-05-31", persist=False)
    june = compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=False)
    assert may.contains_estimates == 1
    assert june.contains_estimates == 0


def test_compute_period_balance_offline_month_returns_none_percentage(conn):
    result = compute_period_balance(conn, "2026-02-01", "2026-02-28", persist=False)
    assert result.total_in == 0.0
    assert result.total_out == 0.0
    assert result.fit0101_under_pct is None


def test_compute_period_balance_persists_and_is_reproducible(conn):
    first = compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=True)

    (count,) = conn.execute("SELECT COUNT(*) FROM balance_result").fetchone()
    assert count == 1

    conn.execute("DELETE FROM balance_result")
    conn.commit()

    second = compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=True)
    assert second.fit0101_under_kl == pytest.approx(first.fit0101_under_kl, abs=1e-9)
    assert second.total_in == pytest.approx(first.total_in, abs=1e-9)


def test_recomputing_same_period_updates_not_duplicates(conn):
    compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=True)
    compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=True)

    (count,) = conn.execute(
        "SELECT COUNT(*) FROM balance_result WHERE period_start = '2026-06-01'"
    ).fetchone()
    assert count == 1


def test_config_changes_do_not_affect_saved_historical_results(conn, tmp_path, monkeypatch):
    before = compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=False)

    # Simulate a future re-estimation of the *current* config.toml, which
    # must not retroactively change June's figure (June has its own
    # config_history row from the import).
    fake_config = tmp_path / "config.toml"
    fake_config.write_text(
        """
[chemicals]
base_litres_per_batch = 9.99
acid_factor = 9.99
ferrous_factor = 9.99
h2o2_factor = 9.99
caustic_factor = 9.99

[plant]
batch_volume_kl = 9.99
max_daily_kl = 58.0
typical_daily_min_kl = 28.0
typical_daily_max_kl = 48.0
"""
    )
    monkeypatch.setattr("gtp.balance.CONFIG_TOML_PATH", fake_config)

    after = compute_period_balance(conn, "2026-06-01", "2026-06-30", persist=False)
    assert after.fit0101_under_kl == pytest.approx(before.fit0101_under_kl, abs=1e-9)


def test_compute_period_balance_rejects_incomplete_range(conn):
    # August only has readings through the 14th; the full month must refuse.
    with pytest.raises(ValueError):
        compute_period_balance(conn, "2026-08-01", "2026-08-31", persist=False)


def test_find_bounding_snapshot_picks_nearest_at_or_before(conn):
    snap = find_bounding_snapshot(conn, "2026-07-31")
    assert snap.taken_at.startswith("2026-07-31")


def test_resolve_config_uses_historical_value_for_july(conn):
    # July sits in the second config era (the April 2026 re-estimation)
    config = resolve_config(conn, "2026-07-31")
    assert config.batch_volume_kl == pytest.approx(
        EXPECTED_FIGURES["config_eras"]["2026-04-01"]["batch_volume_kl"]
    )
    assert config.chem_acid_factor == pytest.approx(1.00)


def test_get_daily_readings_returns_ordered_rows(conn):
    rows = get_daily_readings(conn, "2026-07-01", "2026-07-03")
    assert [r.date for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03"]
