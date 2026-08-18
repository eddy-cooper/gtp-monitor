"""Pure payload builders for the web dashboard. No Flask imports here,
so this file is testable with plain pytest -- the routes in app.py just
call these and return the result as JSON. Plays the same role
gui/format.py plays for the tkinter GUI, and reuses its column specs and
row formatters so both interfaces display identical strings.
"""

import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from gtp.chart import TrendPoint
from gtp.gui.format import (
    BALANCE_COLUMNS,
    READINGS_COLUMNS,
    SNAPSHOTS_COLUMNS,
    format_balance_row,
    format_reading_row,
    format_snapshot_row,
)
from gtp.report import StatusView

CONFIG_TOML_PATH = Path("config.toml")


@dataclass
class SiteConfig:
    display_name: str
    stale_entry_days: int


def load_site_config_from_toml() -> SiteConfig:
    """[site] in config.toml; defaults keep an older config file working."""
    with open(CONFIG_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    site = data.get("site", {})
    return SiteConfig(
        display_name=site.get("display_name", "Groundwater Treatment Plant"),
        stale_entry_days=site.get("stale_entry_days", 3),
    )


# Alert level -> presentation. "role" keys into the CSS palette; the
# label is the operator-facing wording. Icon shapes live in the JS so
# the badge never relies on color alone.
_BADGE = {
    "OK": {"role": "good", "label": "OK"},
    "WATCH": {"role": "warn", "label": "Watch"},
    "ACTION": {"role": "critical", "label": "Action"},
    "INVESTIGATE": {"role": "serious", "label": "Investigate"},
    "NO_DATA": {"role": "muted", "label": "No data"},
}


def alert_badge(level: str) -> dict:
    badge = dict(_BADGE.get(level, {"role": "muted", "label": level.title()}))
    badge["level"] = level
    return badge


def status_payload(view: StatusView, stale_entry_days: int = 3) -> dict:
    """Everything the header badge and stat tiles display. Display
    strings are formatted here (storage stays unrounded, as everywhere);
    the *_value fields carry raw numbers for the count-up animation.
    """
    if not view.has_balance:
        return {"has_balance": False, "badge": alert_badge("NO_DATA")}

    accelerating_note = None
    if view.alert.accelerating and view.alert.accelerating_mean is not None:
        accelerating_note = (
            f"{view.alert.current_pct * 100:.3f}% is more than double the recent "
            f"{view.alert.accelerating_mean * 100:.3f}% mean"
        )

    trend = None
    if view.trend_direction:
        # trend_previous_period is the period_start date; "Jun 2026"
        # reads better in the delta chip than "2026-06-01".
        previous_month = date.fromisoformat(view.trend_previous_period).strftime("%b %Y")
        trend = {
            "direction": view.trend_direction,
            "previous_pct": f"{view.trend_previous_pct * 100:.3f}%",
            "previous_period": previous_month,
        }

    return {
        "has_balance": True,
        "period_start": view.latest_period_start,
        "period_end": view.latest_period_end,
        "under_kl": f"{view.latest_under_kl:.3f}",
        "under_kl_value": view.latest_under_kl,
        "under_pct": (
            None if view.latest_under_pct is None else f"{view.latest_under_pct * 100:.3f}%"
        ),
        "under_pct_value": (
            None if view.latest_under_pct is None else view.latest_under_pct * 100
        ),
        "contains_estimates": view.latest_contains_estimates,
        "badge": alert_badge(view.alert.level),
        "accelerating": view.alert.accelerating,
        "accelerating_note": accelerating_note,
        "trend": trend,
        "last_entry_date": view.last_entry_date,
        "days_since_last_entry": view.days_since_last_entry,
        "entry_stale": (
            view.days_since_last_entry is not None
            and view.days_since_last_entry > stale_entry_days
        ),
        "lag_note": view.balance_lag_note,
        "block_count": view.unresolved_block_count,
        "warn_count": view.warning_count,
    }


def trend_payload(
    points: list[TrendPoint], action_pct: float, watch_pct: float
) -> dict:
    """Chart.js inputs. under_pct is stored as a fraction; the chart's y
    axis is in percent, so convert here (same conversion chart.py makes).
    None stays None -> JSON null -> a genuine gap in the line.
    """
    return {
        "labels": [p.label for p in points],
        "values": [None if p.under_pct is None else p.under_pct * 100 for p in points],
        "estimated": [p.contains_estimates for p in points],
        "action_pct": action_pct,
        "watch_pct": watch_pct,
    }


def tables_payload(conn: sqlite3.Connection) -> dict:
    """The three read-only datasets, same SELECTs and display formatting
    as the tkinter Data tab (gui/app.py _fetch_dataset). Readings and
    snapshots newest first; balance results chronological to match the
    chart.
    """
    readings = conn.execute(
        "SELECT date, fit0101_reading, fit0101_error, fit0501, op_fraction, "
        "is_estimated, estimate_reason, override_reason, comment FROM daily_reading "
        "WHERE fit0101_reading IS NOT NULL OR fit0501 IS NOT NULL ORDER BY date DESC"
    ).fetchall()
    snapshots = conn.execute(
        "SELECT taken_at, t02, t31, t32, t5, f4s, mains_meter, "
        "other_adjustments, note FROM balance_snapshot ORDER BY taken_at DESC"
    ).fetchall()
    balances = conn.execute(
        "SELECT period_start, period_end, total_in, total_out, mains_used, "
        "chemicals_used, fit0101_under_kl, fit0101_under_pct, contains_estimates "
        "FROM balance_result ORDER BY period_start"
    ).fetchall()
    return {
        "readings": {
            "columns": list(READINGS_COLUMNS),
            "rows": [list(format_reading_row(r)) for r in readings],
        },
        "snapshots": {
            "columns": list(SNAPSHOTS_COLUMNS),
            "rows": [list(format_snapshot_row(r)) for r in snapshots],
        },
        "balances": {
            "columns": list(BALANCE_COLUMNS),
            "rows": [list(format_balance_row(r)) for r in balances],
        },
    }
