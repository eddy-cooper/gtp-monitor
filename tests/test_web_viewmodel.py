"""Tests for the web dashboard's pure payload builders (web/viewmodel.py).
Same idea as test_gui_format.py: no server, no browser, just functions.
"""

import pytest

from gtp.alerts import AlertEvaluation
from gtp.chart import TrendPoint
from gtp.db import init_db
from gtp.report import StatusView
from gtp.web.viewmodel import (
    alert_badge,
    status_payload,
    tables_payload,
    trend_payload,
)


# --- alert_badge ------------------------------------------------------------


@pytest.mark.parametrize(
    "level,role,label",
    [
        ("OK", "good", "OK"),
        ("WATCH", "warn", "Watch"),
        ("ACTION", "critical", "Action"),
        ("INVESTIGATE", "serious", "Investigate"),
        ("NO_DATA", "muted", "No data"),
    ],
)
def test_alert_badge_known_levels(level, role, label):
    badge = alert_badge(level)
    assert badge["role"] == role
    assert badge["label"] == label
    assert badge["level"] == level


def test_alert_badge_unknown_level_degrades_to_muted():
    badge = alert_badge("SOMETHING_NEW")
    assert badge["role"] == "muted"
    assert badge["level"] == "SOMETHING_NEW"


# --- status_payload ---------------------------------------------------------


def _view(**overrides) -> StatusView:
    base = dict(
        has_balance=True,
        latest_period_start="2026-07-01",
        latest_period_end="2026-07-31",
        latest_under_kl=1.234,
        latest_under_pct=0.0055,
        latest_contains_estimates=False,
        alert=AlertEvaluation(
            level="WATCH", accelerating=False, current_pct=0.0055, accelerating_mean=0.003
        ),
        last_entry_date="2026-07-31",
        days_since_last_entry=2,
        balance_lag_note=None,
        unresolved_block_count=0,
        warning_count=3,
        trend_direction="rising",
        trend_previous_period="2026-06-01",
        trend_previous_pct=0.004,
    )
    base.update(overrides)
    return StatusView(**base)


def test_status_payload_no_balance():
    payload = status_payload(StatusView(has_balance=False))
    assert payload["has_balance"] is False
    assert payload["badge"]["role"] == "muted"


def test_status_payload_formats_percent_from_fraction():
    payload = status_payload(_view())
    # under_pct is stored as a fraction; display is percent at 3 dp
    assert payload["under_pct"] == "0.550%"
    assert payload["under_pct_value"] == pytest.approx(0.55)
    assert payload["under_kl"] == "1.234"
    assert payload["badge"]["level"] == "WATCH"


def test_status_payload_none_percent_stays_none():
    payload = status_payload(
        _view(latest_under_pct=None, alert=AlertEvaluation("NO_DATA", False, None, None),
              trend_direction=None, trend_previous_period=None, trend_previous_pct=None)
    )
    assert payload["under_pct"] is None
    assert payload["under_pct_value"] is None
    assert payload["trend"] is None


def test_status_payload_accelerating_note():
    payload = status_payload(
        _view(alert=AlertEvaluation("WATCH", True, 0.0085, 0.004))
    )
    assert payload["accelerating"] is True
    assert "0.850%" in payload["accelerating_note"]
    assert "0.400%" in payload["accelerating_note"]


def test_status_payload_not_accelerating_has_no_note():
    payload = status_payload(_view())
    assert payload["accelerating"] is False
    assert payload["accelerating_note"] is None


def test_status_payload_trend():
    payload = status_payload(_view())
    assert payload["trend"] == {
        "direction": "rising",
        "previous_pct": "0.400%",
        "previous_period": "Jun 2026",
    }


@pytest.mark.parametrize(
    "days,stale_after,expected",
    [(2, 3, False), (3, 3, False), (4, 3, True), (None, 3, False), (10, 14, False)],
)
def test_status_payload_entry_staleness(days, stale_after, expected):
    payload = status_payload(_view(days_since_last_entry=days), stale_entry_days=stale_after)
    assert payload["entry_stale"] is expected


# --- trend_payload ----------------------------------------------------------


def test_trend_payload_converts_fraction_to_percent_and_keeps_gaps():
    points = [
        TrendPoint("2026-05-01", "2026-05-31", "May 2026", None, False),
        TrendPoint("2026-06-01", "2026-06-30", "Jun 2026", 0.0055, False),
        TrendPoint("2026-07-01", "2026-07-31", "Jul 2026", 0.0085, True),
    ]
    payload = trend_payload(points, action_pct=1.0, watch_pct=0.5)
    assert payload["labels"] == ["May 2026", "Jun 2026", "Jul 2026"]
    assert payload["values"][0] is None  # gap, not zero
    assert payload["values"][1] == pytest.approx(0.55)
    assert payload["values"][2] == pytest.approx(0.85)
    assert payload["estimated"] == [False, False, True]
    assert payload["action_pct"] == 1.0
    assert payload["watch_pct"] == 0.5


def test_trend_payload_empty():
    payload = trend_payload([], action_pct=1.0, watch_pct=0.5)
    assert payload["labels"] == []
    assert payload["values"] == []


# --- tables_payload ---------------------------------------------------------


def test_tables_payload_shapes_and_order(tmp_path):
    conn = init_db(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0101_error, fit0501, op_fraction) "
        "VALUES ('2026-07-01', 40.0, 0.0, 38.0, 1.0)"
    )
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0101_error, fit0501, op_fraction) "
        "VALUES ('2026-07-02', 41.5, 0.0, 39.0, 1.0)"
    )
    # placeholder row (both flows NULL) must be skipped, same as the GUI
    conn.execute("INSERT INTO daily_reading (date) VALUES ('2026-07-03')")
    conn.execute(
        "INSERT INTO balance_snapshot (taken_at, t02, t31, t32, t5, f4s, mains_meter) "
        "VALUES ('2026-07-31T09:00:00', 1.0, 2.0, 3.0, 4.0, 0.0, 100.0)"
    )
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, total_in, total_out, "
        "mains_used, chemicals_used, fit0101_under_kl, fit0101_under_pct, contains_estimates) "
        "VALUES ('2026-07-01', '2026-07-31', 1000.0, 990.0, 5.0, 0.5, 4.5, 0.0045, 1)"
    )
    conn.commit()

    payload = tables_payload(conn)

    assert len(payload["readings"]["rows"]) == 2  # placeholder skipped
    assert payload["readings"]["rows"][0][0] == "2026-07-02"  # newest first
    assert payload["readings"]["columns"][0] == "Date"

    assert len(payload["snapshots"]["rows"]) == 1
    assert payload["snapshots"]["rows"][0][0] == "2026-07-31T09:00:00"

    assert len(payload["balances"]["rows"]) == 1
    row = payload["balances"]["rows"][0]
    assert row[0] == "2026-07-01 to 2026-07-31"
    assert row[6] == "0.450"  # percent, 3 dp, display only
    assert row[7] == "yes"
