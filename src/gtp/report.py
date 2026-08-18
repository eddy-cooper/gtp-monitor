"""Status/report view (gtp status) and the alert log, per BUILD_PLAN.md
Phase 4 and DATA_SPEC.md §5.

gtp status is deliberately read-only: it reports the latest
already-calculated balance rather than trying to compute a fresh one on
the spot (the most recent days often don't have real readings entered
yet, and this tool never guesses at those). Run gtp balance first for a
fresher figure.
"""

import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from gtp.alerts import AlertConfig, AlertEvaluation, evaluate_alert, load_alert_config_from_toml
from gtp.alerts import trailing_periods_pct
from gtp.validate import ValidationConfig, load_validation_config_from_toml, run_check

CONFIG_TOML_PATH = Path("config.toml")


def load_log_path_from_toml() -> Path:
    with open(CONFIG_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    return Path(data["logging"]["log_path"])


@dataclass
class StatusView:
    has_balance: bool
    latest_period_start: str | None = None
    latest_period_end: str | None = None
    latest_under_kl: float | None = None
    latest_under_pct: float | None = None
    latest_contains_estimates: bool = False
    alert: AlertEvaluation | None = None
    last_entry_date: str | None = None
    days_since_last_entry: int | None = None
    balance_lag_note: str | None = None
    unresolved_block_count: int = 0
    warning_count: int = 0
    trend_direction: str | None = None
    trend_previous_period: str | None = None
    trend_previous_pct: float | None = None


def trend_direction(current_pct: float | None, previous_pct: float | None) -> str | None:
    if current_pct is None or previous_pct is None:
        return None
    if current_pct > previous_pct:
        return "rising"
    if current_pct < previous_pct:
        return "falling"
    return "flat"


def build_status_view(
    conn: sqlite3.Connection,
    today: str | None = None,
    alert_config: AlertConfig | None = None,
    validation_config: ValidationConfig | None = None,
) -> StatusView:
    today = today or datetime.now().date().isoformat()
    alert_config = alert_config or load_alert_config_from_toml()

    latest = conn.execute(
        "SELECT period_start, period_end, fit0101_under_kl, fit0101_under_pct, "
        "contains_estimates FROM balance_result ORDER BY period_start DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        return StatusView(has_balance=False)

    period_start, period_end, under_kl, under_pct, contains_estimates = latest
    previous_pcts = trailing_periods_pct(conn, period_start, alert_config.accelerating_window)
    alert = evaluate_alert(under_pct, previous_pcts, alert_config)

    (last_entry_date,) = conn.execute(
        "SELECT MAX(date) FROM daily_reading WHERE fit0101_reading IS NOT NULL"
    ).fetchone()
    days_since = None
    if last_entry_date:
        days_since = (date.fromisoformat(today) - date.fromisoformat(last_entry_date)).days

    balance_lag_note = None
    if last_entry_date and last_entry_date > period_end:
        balance_lag_note = (
            f"Newer readings exist (through {last_entry_date}) than the last "
            f"computed balance ({period_end}) reflects — run gtp balance to refresh."
        )

    validation_config = validation_config or load_validation_config_from_toml()
    check_report = run_check(conn, config=validation_config, today=today)
    unresolved = [f for f in check_report.unresolved_blocks if f.subject[:10] >= period_start]
    warnings = [f for f in check_report.warnings if f.subject[:10] >= period_start]

    prev_row = conn.execute(
        "SELECT period_start, fit0101_under_pct FROM balance_result "
        "WHERE period_start < ? AND fit0101_under_pct IS NOT NULL "
        "ORDER BY period_start DESC LIMIT 1",
        (period_start,),
    ).fetchone()
    trend_prev_period, trend_prev_pct = prev_row if prev_row else (None, None)

    return StatusView(
        has_balance=True,
        latest_period_start=period_start,
        latest_period_end=period_end,
        latest_under_kl=under_kl,
        latest_under_pct=under_pct,
        latest_contains_estimates=bool(contains_estimates),
        alert=alert,
        last_entry_date=last_entry_date,
        days_since_last_entry=days_since,
        balance_lag_note=balance_lag_note,
        unresolved_block_count=len(unresolved),
        warning_count=len(warnings),
        trend_direction=trend_direction(under_pct, trend_prev_pct),
        trend_previous_period=trend_prev_period,
        trend_previous_pct=trend_prev_pct,
    )


def format_alert_log_line(status: StatusView, logged_at: str) -> str:
    return (
        f"{logged_at} | period {status.latest_period_start}–{status.latest_period_end} "
        f"| under_pct={status.latest_under_pct * 100:.3f}% | level={status.alert.level} "
        f"| accelerating={status.alert.accelerating}"
    )


def write_alert_log(
    status: StatusView, log_path: Path | None = None, logged_at: str | None = None
) -> bool:
    """Appends one line if the alert is worth recording (anything other
    than a routine OK/no-data, or an accelerating flag). Returns whether
    it wrote, so the caller can tell the user what happened.
    """
    if not status.has_balance or status.alert is None:
        return False
    if status.alert.level in ("OK", "NO_DATA") and not status.alert.accelerating:
        return False
    log_path = log_path or load_log_path_from_toml()
    logged_at = logged_at or datetime.now().isoformat(timespec="seconds")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(format_alert_log_line(status, logged_at) + "\n")
    return True
