"""Pure formatting/parsing helpers for the GUI. No tkinter imports here,
so this file is testable with plain pytest -- the tkinter widgets in
app.py just call these and display the result.
"""

from gtp.report import StatusView
from gtp.validate import Finding


def parse_kl(text: str, field_name: str) -> float:
    """Raises ValueError with a message fit to show the user directly."""
    text = text.strip()
    if not text:
        raise ValueError(f"{field_name} is required.")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{field_name} must be a number, got {text!r}.")


def parse_optional_kl(text: str, field_name: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    return parse_kl(text, field_name)


def format_finding(f: Finding) -> str:
    return f"[{f.rule}] {f.entity} {f.subject}: {f.message}"


# --- Data-tab tables --------------------------------------------------------
#
# Column specs and row formatters for the read-only spreadsheet view.
# Each formatter takes a raw sqlite row (in the column order its SELECT
# in app.py uses) and returns display strings: volumes at 2 dp (display
# only -- storage is never rounded), None shown as blank, never as 0.

READINGS_COLUMNS = (
    "Date", "FIT0101 reading (kL)", "FIT0101 error (kL)", "FIT0501 (kL)",
    "Op fraction", "Estimated", "Override reason", "Comment",
)
SNAPSHOTS_COLUMNS = (
    "Taken at", "T02 (kL)", "T31 (kL)", "T32 (kL)", "T5 (kL)", "F4s (kL)",
    "Mains meter (kL)", "Other adj (kL)", "Note",
)
BALANCE_COLUMNS = (
    "Period", "Total in (kL)", "Total out (kL)", "Mains used (kL)",
    "Chemicals (kL)", "Under (kL)", "Under (%)", "Has estimates",
)


def _num(value: float | None, places: int = 2) -> str:
    return "" if value is None else f"{value:.{places}f}"


def _text(value: str | None) -> str:
    return value or ""


def format_reading_row(row: tuple) -> tuple[str, ...]:
    """row: (date, fit0101_reading, fit0101_error, fit0501, op_fraction,
    is_estimated, estimate_reason, override_reason, comment)"""
    date, reading, error, fit0501, op_fraction, is_estimated, reason, override, comment = row
    estimated = f"yes ({reason})" if is_estimated else ""
    return (
        date, _num(reading), _num(error), _num(fit0501), _num(op_fraction),
        estimated, _text(override), _text(comment),
    )


def format_snapshot_row(row: tuple) -> tuple[str, ...]:
    """row: (taken_at, t02, t31, t32, t5, f4s, mains_meter,
    other_adjustments, note)"""
    taken_at, t02, t31, t32, t5, f4s, mains, other_adj, note = row
    return (
        taken_at, _num(t02), _num(t31), _num(t32), _num(t5), _num(f4s),
        _num(mains), _num(other_adj), _text(note),
    )


def format_balance_row(row: tuple) -> tuple[str, ...]:
    """row: (period_start, period_end, total_in, total_out, mains_used,
    chemicals_used, fit0101_under_kl, fit0101_under_pct, contains_estimates)"""
    start, end, total_in, total_out, mains, chems, under_kl, under_pct, has_est = row
    return (
        f"{start} to {end}", _num(total_in), _num(total_out), _num(mains),
        _num(chems, 3), _num(under_kl, 3),
        "" if under_pct is None else f"{under_pct * 100:.3f}",
        "yes" if has_est else "",
    )


def format_status_lines(view: StatusView) -> list[str]:
    """Re-derives the same content cli.py's `status` command prints, as
    plain lines a Text widget can display -- no printing here.
    """
    if not view.has_balance:
        return ['No balance has been computed yet. Use "Recompute balance..." below.']

    lines = [
        f"Latest balance:      {view.latest_period_start} to {view.latest_period_end}",
        "FIT0101 under:       "
        + (
            f"{view.latest_under_kl:.3f} kL  ({view.latest_under_pct * 100:.3f}%)"
            if view.latest_under_pct is not None
            else f"{view.latest_under_kl:.3f} kL  (n/a)"
        ),
        "Contains estimates:  "
        + ("yes (back-filled days in this period)" if view.latest_contains_estimates else "no"),
        "",
        f"Alert level:         {view.alert.level}",
    ]

    accel_note = ""
    if view.alert.accelerating and view.alert.accelerating_mean is not None:
        accel_note = (
            f" ({view.alert.current_pct * 100:.3f}% is more than double the recent "
            f"{view.alert.accelerating_mean * 100:.3f}% mean)"
        )
    lines.append(f"Accelerating:        {'yes' if view.alert.accelerating else 'no'}{accel_note}")

    if view.trend_direction:
        lines.append(
            f"Trend:               {view.trend_direction} "
            f"(vs {view.trend_previous_pct * 100:.3f}% in {view.trend_previous_period})"
        )

    lines.append("")
    last_entry = f"Last entry:          {view.last_entry_date}"
    if view.days_since_last_entry is not None:
        last_entry += f" ({view.days_since_last_entry} days ago)"
    lines.append(last_entry)
    if view.balance_lag_note:
        lines.append(f"  Note: {view.balance_lag_note}")

    lines.append("")
    lines.append(
        f"Validation:          {view.unresolved_block_count} unresolved BLOCK, "
        f"{view.warning_count} WARN since {view.latest_period_start}"
    )
    return lines
