"""Tests for gtp.gui.format -- the pure display/parsing helpers behind
the GUI's Status tab and entry forms.
"""

import pytest

from gtp.alerts import AlertEvaluation
from gtp.gui.format import (
    BALANCE_COLUMNS,
    READINGS_COLUMNS,
    SNAPSHOTS_COLUMNS,
    format_balance_row,
    format_finding,
    format_reading_row,
    format_snapshot_row,
    format_status_lines,
    parse_kl,
    parse_optional_kl,
)
from gtp.report import StatusView
from gtp.validate import BLOCK, Finding


# --- parse_kl / parse_optional_kl ---------------------------------------


def test_parse_kl_valid_number():
    assert parse_kl("40.5", "FIT0101 reading") == 40.5


def test_parse_kl_strips_whitespace():
    assert parse_kl("  40.5  ", "FIT0101 reading") == 40.5


def test_parse_kl_blank_raises():
    with pytest.raises(ValueError, match="is required"):
        parse_kl("", "FIT0101 reading")


def test_parse_kl_garbage_raises():
    with pytest.raises(ValueError, match="must be a number"):
        parse_kl("abc", "FIT0101 reading")


def test_parse_optional_kl_blank_returns_none():
    assert parse_optional_kl("", "other_adjustments") is None


def test_parse_optional_kl_present_parses():
    assert parse_optional_kl("1.5", "other_adjustments") == 1.5


def test_parse_optional_kl_garbage_raises():
    with pytest.raises(ValueError, match="must be a number"):
        parse_optional_kl("nope", "other_adjustments")


# --- format_finding -------------------------------------------------------


def test_format_finding():
    f = Finding("V1", BLOCK, "daily_reading", "2026-08-14", "fit0101_actual (-5.0) is negative.")
    assert format_finding(f) == "[V1] daily_reading 2026-08-14: fit0101_actual (-5.0) is negative."


# --- format_status_lines --------------------------------------------------


def test_format_status_lines_no_balance():
    lines = format_status_lines(StatusView(has_balance=False))
    assert len(lines) == 1
    assert "No balance has been computed yet" in lines[0]


def test_format_status_lines_has_balance():
    view = StatusView(
        has_balance=True,
        latest_period_start="2026-08-01",
        latest_period_end="2026-08-11",
        latest_under_kl=0.401,
        latest_under_pct=0.0092209,
        latest_contains_estimates=False,
        alert=AlertEvaluation(level="WATCH", accelerating=False, current_pct=0.0092209, accelerating_mean=None),
        last_entry_date="2026-08-11",
        days_since_last_entry=3,
        balance_lag_note=None,
        unresolved_block_count=0,
        warning_count=2,
        trend_direction="rising",
        trend_previous_period="2026-07-01",
        trend_previous_pct=0.004,
    )
    lines = format_status_lines(view)
    text = "\n".join(lines)
    assert "2026-08-01 to 2026-08-11" in text
    assert "0.401 kL" in text
    assert "0.922" in text  # under_pct * 100
    assert "WATCH" in text
    assert "rising" in text
    assert "2 WARN" in text


def test_format_status_lines_includes_balance_lag_note():
    view = StatusView(
        has_balance=True,
        latest_period_start="2026-07-01",
        latest_period_end="2026-07-31",
        latest_under_kl=1.0,
        latest_under_pct=0.01,
        alert=AlertEvaluation(level="OK", accelerating=False, current_pct=0.01, accelerating_mean=None),
        last_entry_date="2026-08-05",
        days_since_last_entry=0,
        balance_lag_note="Newer readings exist (through 2026-08-05) than the last computed balance reflects.",
    )
    text = "\n".join(format_status_lines(view))
    assert "Newer readings exist" in text


def test_format_reading_row_rounds_and_flags():
    row = ("2026-08-11", 40.0, 0.456, 40.8123, 1.0, 1, "PLC freeze", "checked on site", "windy day")
    out = format_reading_row(row)
    assert len(out) == len(READINGS_COLUMNS)
    assert out[0] == "2026-08-11"
    assert out[1] == "40.00"
    assert out[2] == "0.46"       # 2 dp display rounding
    assert out[3] == "40.81"
    assert out[5] == "yes (PLC freeze)"
    assert out[6] == "checked on site"
    assert out[7] == "windy day"


def test_format_reading_row_none_stays_blank_not_zero():
    row = ("2026-08-12", None, 0.0, None, None, 0, None, None, None)
    out = format_reading_row(row)
    assert out[1] == ""           # missing reading is blank, never 0
    assert out[2] == "0.00"       # a real stored zero still shows
    assert out[3] == ""
    assert out[4] == ""
    assert out[5] == ""           # not estimated -> blank, not "no"
    assert out[6] == ""
    assert out[7] == ""


def test_format_snapshot_row():
    row = ("2026-08-10T09:00:00", 100.0, 50.5, 50.0, 20.0, 0.0, 1700.456, 0.0, "weekly")
    out = format_snapshot_row(row)
    assert len(out) == len(SNAPSHOTS_COLUMNS)
    assert out[0] == "2026-08-10T09:00:00"
    assert out[6] == "1700.46"
    assert out[8] == "weekly"


def test_format_balance_row():
    row = ("2026-08-01", "2026-08-11", 440.0, 435.5, 10.2, 0.4567, 4.0759, 0.0092209, 1)
    out = format_balance_row(row)
    assert len(out) == len(BALANCE_COLUMNS)
    assert out[0] == "2026-08-01 to 2026-08-11"
    assert out[4] == "0.457"      # chemicals at 3 dp, matching the CLI
    assert out[5] == "4.076"      # under kL at 3 dp, matching the CLI
    assert out[6] == "0.922"      # fraction -> percent
    assert out[7] == "yes"


def test_format_balance_row_none_pct_blank():
    row = ("2026-05-01", "2026-05-31", 0.0, 0.0, 0.0, 0.0, 0.0, None, 0)
    out = format_balance_row(row)
    assert out[6] == ""           # zero-throughput month: no %, not 0
    assert out[7] == ""


def test_format_status_lines_accelerating_note():
    view = StatusView(
        has_balance=True,
        latest_period_start="2026-08-01",
        latest_period_end="2026-08-11",
        latest_under_kl=0.9,
        latest_under_pct=0.0092209,
        alert=AlertEvaluation(level="WATCH", accelerating=True, current_pct=0.0092209, accelerating_mean=0.004),
    )
    text = "\n".join(format_status_lines(view))
    assert "Accelerating:        yes" in text
    assert "more than double" in text
