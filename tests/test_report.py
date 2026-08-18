"""Tests for gtp status's content and the alert log, per BUILD_PLAN.md Phase 4."""

import sqlite3
from pathlib import Path

from gtp.alerts import AlertConfig
from gtp.db import init_db
from gtp.report import (
    StatusView,
    build_status_view,
    format_alert_log_line,
    trend_direction,
    write_alert_log,
)
from gtp.validate import ValidationConfig

ALERT_CONFIG = AlertConfig(
    action_pct=1.0, watch_pct=0.5, accelerating_factor=2.0,
    accelerating_window=3, investigate_pct=-0.5,
)
VALIDATION_CONFIG = ValidationConfig(
    max_daily_kl=55.0, typical_daily_min_kl=30.0, typical_daily_max_kl=45.0,
    consecutive_freeze_days=3, mains_increase_factor=3.0, mains_median_window=6,
    daily_imbalance_kl=15.0,
)


# --- trend_direction ---------------------------------------------------


def test_trend_direction_rising():
    assert trend_direction(0.009, 0.005) == "rising"


def test_trend_direction_falling():
    assert trend_direction(0.003, 0.005) == "falling"


def test_trend_direction_flat():
    assert trend_direction(0.005, 0.005) == "flat"


def test_trend_direction_none_when_either_missing():
    assert trend_direction(None, 0.005) is None
    assert trend_direction(0.005, None) is None


# --- format_alert_log_line ----------------------------------------------


def test_format_alert_log_line():
    from gtp.alerts import AlertEvaluation

    status = StatusView(
        has_balance=True, latest_period_start="2026-08-01", latest_period_end="2026-08-11",
        latest_under_pct=0.0092209,
        alert=AlertEvaluation(level="WATCH", accelerating=True, current_pct=0.0092209, accelerating_mean=0.004),
    )
    line = format_alert_log_line(status, "2026-08-13T09:00:00")
    assert "WATCH" in line
    assert "2026-08-01" in line and "2026-08-11" in line
    assert "0.922" in line
    assert "True" in line


# --- build_status_view ---------------------------------------------------


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, fit0101_under_kl, "
        "fit0101_under_pct, contains_estimates) VALUES "
        "('2026-06-01','2026-06-30', 2.67, 0.0031183, 0)"
    )
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, fit0101_under_kl, "
        "fit0101_under_pct, contains_estimates) VALUES "
        "('2026-07-01','2026-07-31', 6.224, 0.0051195, 1)"
    )
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, fit0101_under_kl, "
        "fit0101_under_pct, contains_estimates) VALUES "
        "('2026-08-01','2026-08-11', 4.076, 0.0092209, 1)"
    )
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-08-11', 43.3, 45.3, 1.0, 0, 'now')"
    )
    conn.commit()


def test_build_status_view_no_balance_yet(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    view = build_status_view(conn, today="2026-08-13", alert_config=ALERT_CONFIG, validation_config=VALIDATION_CONFIG)
    assert view.has_balance is False


def test_build_status_view_real_shape(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    _seed(conn)

    view = build_status_view(conn, today="2026-08-13", alert_config=ALERT_CONFIG, validation_config=VALIDATION_CONFIG)

    assert view.has_balance is True
    assert view.latest_period_start == "2026-08-01"
    assert view.latest_period_end == "2026-08-11"
    assert view.latest_contains_estimates is True
    assert view.alert.level == "WATCH"
    assert view.alert.accelerating is True
    assert view.last_entry_date == "2026-08-11"
    assert view.days_since_last_entry == 2
    assert view.trend_direction == "rising"
    assert view.trend_previous_period == "2026-07-01"


def test_build_status_view_flags_stale_balance(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    _seed(conn)
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0501, op_fraction, "
        "is_estimated, entered_at) VALUES ('2026-08-12', 40.0, 39.5, 1.0, 0, 'now')"
    )
    conn.commit()

    view = build_status_view(conn, today="2026-08-13", alert_config=ALERT_CONFIG, validation_config=VALIDATION_CONFIG)
    assert view.balance_lag_note is not None
    assert "2026-08-12" in view.balance_lag_note


# --- write_alert_log -----------------------------------------------------


def test_write_alert_log_writes_for_watch_level(tmp_path: Path):
    from gtp.alerts import AlertEvaluation

    status = StatusView(
        has_balance=True, latest_period_start="2026-08-01", latest_period_end="2026-08-11",
        latest_under_pct=0.0092209,
        alert=AlertEvaluation(level="WATCH", accelerating=True, current_pct=0.0092209, accelerating_mean=0.004),
    )
    log_path = tmp_path / "alerts.log"
    wrote = write_alert_log(status, log_path=log_path, logged_at="2026-08-13T09:00:00")
    assert wrote is True
    assert log_path.exists()
    assert "WATCH" in log_path.read_text()


def test_write_alert_log_skips_ok_and_not_accelerating(tmp_path: Path):
    from gtp.alerts import AlertEvaluation

    status = StatusView(
        has_balance=True, latest_period_start="2026-03-01", latest_period_end="2026-03-31",
        latest_under_pct=0.0026,
        alert=AlertEvaluation(level="OK", accelerating=False, current_pct=0.0026, accelerating_mean=0.002),
    )
    log_path = tmp_path / "alerts.log"
    wrote = write_alert_log(status, log_path=log_path, logged_at="2026-08-13T09:00:00")
    assert wrote is False
    assert not log_path.exists()


def test_write_alert_log_skips_when_no_balance(tmp_path: Path):
    status = StatusView(has_balance=False)
    log_path = tmp_path / "alerts.log"
    wrote = write_alert_log(status, log_path=log_path, logged_at="2026-08-13T09:00:00")
    assert wrote is False
