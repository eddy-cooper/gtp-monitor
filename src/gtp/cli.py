"""Command-line entry point for the GTP monitoring tool."""

import calendar
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from gtp.balance import compute_period_balance
from gtp.chart import build_trend_chart
from gtp.db import DEFAULT_DB_PATH, init_db
from gtp.importer import import_workbook
from gtp.models import BalanceSnapshot, DailyReading
from gtp.report import build_status_view, write_alert_log
from gtp.store import save_balance_snapshot, save_daily_reading
from gtp.validate import (
    BLOCK,
    load_validation_config_from_toml,
    recent_balance_snapshots,
    recent_daily_readings,
    run_check,
    validate_balance_snapshot,
    validate_daily_reading,
)

# The site's display name lives in config.toml ([site]), not in code --
# this help string stays generic so the code is site-agnostic.
app = typer.Typer(
    name="gtp",
    help="Groundwater treatment plant monitoring tool.",
    no_args_is_help=True,
)


def _today() -> str:
    return datetime.now().date().isoformat()


@app.command()
def version() -> None:
    """Print the tool version."""
    from gtp import __version__

    typer.echo(__version__)


@app.command(name="import")
def import_legacy(
    path: Path = typer.Argument(..., help="Path to the legacy .xlsx workbook."),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Import the legacy workbook. Safe to re-run (idempotent)."""
    if not path.exists():
        typer.echo(f"File not found: {path}", err=True)
        raise typer.Exit(code=1)

    summary = import_workbook(path, db_path)
    typer.echo(f"Sheets imported:      {summary.sheets_imported}")
    typer.echo(f"Days imported:        {summary.days_imported}")
    typer.echo(f"Snapshots written:    {summary.snapshots_imported}")
    typer.echo(f"Service events:       {summary.service_events_imported}")
    typer.echo(f"Legacy balance notes: {summary.legacy_notes_imported}")
    typer.echo(f"Config history rows:  {summary.config_rows_imported}")
    if summary.warnings:
        typer.echo(f"Warnings ({len(summary.warnings)}):")
        for w in summary.warnings:
            typer.echo(f"  - {w}")


def _validate_date(date: str) -> str:
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        typer.echo(f"Date must be ISO YYYY-MM-DD, got: {date!r}", err=True)
        raise typer.Exit(code=1)
    return date


@app.command()
def add(
    date: str = typer.Option(..., help="ISO date YYYY-MM-DD."),
    fit0101_reading: float = typer.Option(..., help="Raw inflow total for the day, kL."),
    fit0501: float = typer.Option(..., help="Discharge to sewer for the day, kL."),
    op_fraction: float = typer.Option(..., help="Fraction of the day the plant ran."),
    fit0101_error: float = typer.Option(0.0, help="Known false/phantom flow to subtract, kL."),
    bore_status: Optional[str] = typer.Option(None),
    comment: Optional[str] = typer.Option(None),
    estimated: bool = typer.Option(False, "--estimated", help="Mark this value as back-filled, not measured."),
    reason: Optional[str] = typer.Option(None, help="Required with --estimated."),
    override: Optional[str] = typer.Option(None, "--override", help='Reason to save despite a BLOCK finding, e.g. --override "confirmed with site visit".'),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Add a single day's reading. Refuses a date that already exists."""
    _validate_date(date)
    if estimated and not reason:
        typer.echo("--reason is required when --estimated is set.", err=True)
        raise typer.Exit(code=1)

    conn = init_db(db_path)
    existing = conn.execute(
        "SELECT 1 FROM daily_reading WHERE date = ?", (date,)
    ).fetchone()
    if existing:
        typer.echo(f"A reading for {date} already exists. Refusing to overwrite.", err=True)
        raise typer.Exit(code=1)

    candidate = DailyReading(
        date=date, fit0101_reading=fit0101_reading, fit0101_error=fit0101_error,
        fit0501=fit0501, op_fraction=op_fraction, bore_status=bore_status,
        comment=comment, is_estimated=int(estimated), estimate_reason=reason,
    )
    config = load_validation_config_from_toml()
    recent = recent_daily_readings(conn, date)
    findings = validate_daily_reading(candidate, recent, config, today=_today())

    blocks = [f for f in findings if f.level == BLOCK]
    for f in findings:
        if f.level != BLOCK:
            typer.echo(f"WARN [{f.rule}] {f.message}")
    if blocks:
        for f in blocks:
            typer.echo(f"BLOCK [{f.rule}] {f.message}", err=True)
        if not override:
            typer.echo('Re-run with --override "<reason>" to proceed.', err=True)
            raise typer.Exit(code=1)
        for f in blocks:
            typer.echo(f"OVERRIDDEN: [{f.rule}] {f.message}")
    elif override:
        typer.echo("--override not needed — no BLOCK-level finding; ignoring.")
        override = None

    typer.echo("About to save:")
    typer.echo(f"  date:            {date}")
    typer.echo(f"  fit0101_reading: {fit0101_reading}")
    typer.echo(f"  fit0101_error:   {fit0101_error}")
    typer.echo(f"  fit0501:         {fit0501}")
    typer.echo(f"  op_fraction:     {op_fraction}")
    typer.echo(f"  bore_status:     {bore_status}")
    typer.echo(f"  comment:         {comment}")
    typer.echo(f"  estimated:       {bool(estimated)}" + (f" ({reason})" if estimated else ""))
    if not yes and not typer.confirm("Save this entry?"):
        typer.echo("Cancelled. Nothing saved.")
        raise typer.Exit(code=0)

    save_daily_reading(conn, candidate, override)
    typer.echo(f"Added reading for {date}.")


@app.command()
def snapshot(
    taken_at: str = typer.Option(..., help="ISO datetime, e.g. 2026-08-13T09:00:00."),
    t02: float = typer.Option(...),
    t31: float = typer.Option(...),
    t32: float = typer.Option(...),
    t5: float = typer.Option(...),
    mains_meter: float = typer.Option(..., help="Cumulative mains meter reading, kL."),
    f4s: float = typer.Option(0.0, help="Normally 0; isolated from system."),
    other_adjustments: float = typer.Option(0.0),
    other_note: Optional[str] = typer.Option(None),
    note: Optional[str] = typer.Option(None),
    override: Optional[str] = typer.Option(None, "--override", help='Reason to save despite a BLOCK finding.'),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Record a manual tank-level snapshot."""
    try:
        datetime.fromisoformat(taken_at)
    except ValueError:
        typer.echo(f"taken-at must be an ISO datetime, got: {taken_at!r}", err=True)
        raise typer.Exit(code=1)

    conn = init_db(db_path)
    existing = conn.execute(
        "SELECT 1 FROM balance_snapshot WHERE taken_at = ?", (taken_at,)
    ).fetchone()
    if existing:
        typer.echo(f"A snapshot at {taken_at} already exists. Refusing to overwrite.", err=True)
        raise typer.Exit(code=1)

    candidate = BalanceSnapshot(
        taken_at=taken_at, t02=t02, t31=t31, t32=t32, t5=t5, f4s=f4s,
        mains_meter=mains_meter, other_adjustments=other_adjustments,
        other_note=other_note, note=note,
    )
    config = load_validation_config_from_toml()
    recent = recent_balance_snapshots(conn, taken_at, limit=config.mains_median_window + 1)
    findings = validate_balance_snapshot(candidate, recent, config, today=_today())

    blocks = [f for f in findings if f.level == BLOCK]
    for f in findings:
        if f.level != BLOCK:
            typer.echo(f"WARN [{f.rule}] {f.message}")
    if blocks:
        for f in blocks:
            typer.echo(f"BLOCK [{f.rule}] {f.message}", err=True)
        if not override:
            typer.echo('Re-run with --override "<reason>" to proceed.', err=True)
            raise typer.Exit(code=1)
        for f in blocks:
            typer.echo(f"OVERRIDDEN: [{f.rule}] {f.message}")
    elif override:
        typer.echo("--override not needed — no BLOCK-level finding; ignoring.")
        override = None

    typer.echo("About to save:")
    typer.echo(f"  taken_at:          {taken_at}")
    typer.echo(f"  t02, t31, t32, t5: {t02}, {t31}, {t32}, {t5}")
    typer.echo(f"  f4s:               {f4s}")
    typer.echo(f"  mains_meter:       {mains_meter}")
    typer.echo(f"  other_adjustments: {other_adjustments} ({other_note})")
    if not yes and not typer.confirm("Save this entry?"):
        typer.echo("Cancelled. Nothing saved.")
        raise typer.Exit(code=0)

    save_balance_snapshot(conn, candidate, override)
    typer.echo(f"Recorded snapshot at {taken_at}.")


@app.command()
def override(
    date: Optional[str] = typer.Option(None, help="Date of the daily_reading row, YYYY-MM-DD."),
    taken_at: Optional[str] = typer.Option(None, help="taken_at of the balance_snapshot row."),
    reason: str = typer.Option(..., "--reason"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Attach a stored reason to an existing row, retroactively explaining
    a BLOCK-level finding gtp check turned up (e.g. the January mains
    anomaly, which predates validation and was imported before it existed).
    """
    if bool(date) == bool(taken_at):
        typer.echo("Provide exactly one of --date or --taken-at.", err=True)
        raise typer.Exit(code=1)

    conn = init_db(db_path)
    if date:
        existing = conn.execute("SELECT 1 FROM daily_reading WHERE date = ?", (date,)).fetchone()
        if not existing:
            typer.echo(f"No daily_reading row for {date}.", err=True)
            raise typer.Exit(code=1)
        conn.execute(
            "UPDATE daily_reading SET override_reason = ? WHERE date = ?", (reason, date)
        )
        typer.echo(f"Stored override reason for {date}.")
    else:
        existing = conn.execute(
            "SELECT 1 FROM balance_snapshot WHERE taken_at = ?", (taken_at,)
        ).fetchone()
        if not existing:
            typer.echo(f"No balance_snapshot row for {taken_at}.", err=True)
            raise typer.Exit(code=1)
        conn.execute(
            "UPDATE balance_snapshot SET override_reason = ? WHERE taken_at = ?",
            (reason, taken_at),
        )
        typer.echo(f"Stored override reason for {taken_at}.")
    conn.commit()


def _findings_since(findings: list, since: str) -> list:
    return [f for f in findings if f.subject[:10] >= since]


@app.command()
def check(
    since: Optional[str] = typer.Option(
        None, help="Only show findings on or after this ISO date, e.g. 2026-08-01. "
                    "Full history is still scanned internally -- some rules need older "
                    "context -- only the printed report is narrowed."
    ),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Re-run validation across all existing data. Read-only, never modifies data."""
    if since:
        _validate_date(since)

    conn = init_db(db_path)
    report = run_check(conn)

    unresolved_blocks = report.unresolved_blocks
    overridden_blocks = report.overridden_blocks
    warnings = report.warnings
    estimated_entries = report.estimated_entries
    if since:
        unresolved_blocks = _findings_since(unresolved_blocks, since)
        overridden_blocks = _findings_since(overridden_blocks, since)
        warnings = _findings_since(warnings, since)
        estimated_entries = [(d, r) for d, r in estimated_entries if d >= since]

    header = (
        f"Validation check — {report.scanned_readings} daily readings scanned "
        f"({report.skipped_readings} skipped, not yet entered), "
        f"{report.scanned_snapshots} snapshots scanned."
    )
    if since:
        header += f"\nShowing findings on or after {since} (full history scanned for context)."
    typer.echo(header + "\n")

    typer.echo(f"BLOCK — unresolved ({len(unresolved_blocks)})")
    for f in unresolved_blocks:
        typer.echo(f"  [{f.rule}] {f.entity} {f.subject}: {f.message}")
    typer.echo()

    typer.echo(f"BLOCK — already overridden ({len(overridden_blocks)})")
    for f in overridden_blocks:
        typer.echo(f"  [{f.rule}] {f.entity} {f.subject}: {f.message}")
    typer.echo()

    typer.echo(f"WARN ({len(warnings)})")
    for f in warnings:
        typer.echo(f"  [{f.rule}] {f.entity} {f.subject}: {f.message}")
    typer.echo()

    typer.echo(f"Estimated data — back-filled, not measured ({len(estimated_entries)})")
    for entry_date, entry_reason in estimated_entries:
        typer.echo(f"  {entry_date}: {entry_reason}")
    typer.echo()

    typer.echo(
        f"Summary: {len(unresolved_blocks)} unresolved BLOCK, "
        f"{len(overridden_blocks)} overridden, {len(warnings)} WARN, "
        f"{len(estimated_entries)} estimated days."
    )


@app.command()
def balance(
    month: Optional[str] = typer.Option(None, help="Calendar month, e.g. 2026-07."),
    from_: Optional[str] = typer.Option(None, "--from", help="ISO start date."),
    to: Optional[str] = typer.Option(None, "--to", help="ISO end date."),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Compute (and save) the water balance for a period."""
    if month:
        if from_ or to:
            typer.echo("Use either --month or --from/--to, not both.", err=True)
            raise typer.Exit(code=1)
        try:
            year, mon = (int(x) for x in month.split("-"))
        except ValueError:
            typer.echo(f"--month must be YYYY-MM, got: {month!r}", err=True)
            raise typer.Exit(code=1)
        start_date = f"{year:04d}-{mon:02d}-01"
        end_date = f"{year:04d}-{mon:02d}-{calendar.monthrange(year, mon)[1]:02d}"
    elif from_ and to:
        start_date, end_date = _validate_date(from_), _validate_date(to)
    else:
        typer.echo("Provide --month, or both --from and --to.", err=True)
        raise typer.Exit(code=1)

    conn = init_db(db_path)
    try:
        result = compute_period_balance(conn, start_date, end_date)
    except ValueError as e:
        typer.echo(f"Cannot compute balance: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Period:            {result.period_start} to {result.period_end}")
    typer.echo(f"Total in:          {result.total_in:.2f} kL")
    typer.echo(f"Total out:         {result.total_out:.2f} kL")
    typer.echo(f"Mains used:        {result.mains_used:.2f} kL")
    typer.echo(f"Chemicals used:    {result.chemicals_used:.3f} kL")
    typer.echo(f"Other adjustments: {result.other_adjustments:.2f} kL")
    typer.echo(f"FIT0101 under:     {result.fit0101_under_kl:.3f} kL")
    if result.fit0101_under_pct is None:
        typer.echo("FIT0101 under %:   n/a (zero throughput)")
    else:
        typer.echo(f"FIT0101 under %:   {result.fit0101_under_pct * 100:.3f}%")


@app.command()
def status(db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file.")) -> None:
    """The headline view: latest balance, alert level, trend, and any
    recent outstanding validation issues. Read-only -- run `gtp balance`
    first for a fresher figure if newer readings have been entered.
    """
    conn = init_db(db_path)
    today = _today()
    view = build_status_view(conn, today=today)

    if not view.has_balance:
        typer.echo("No balance has been computed yet. Run `gtp balance` first.")
        raise typer.Exit(code=0)

    typer.echo(f"Status as of {today}\n")
    typer.echo(f"Latest balance:      {view.latest_period_start} to {view.latest_period_end}")
    typer.echo(
        f"FIT0101 under:       {view.latest_under_kl:.3f} kL"
        + (f"  ({view.latest_under_pct * 100:.3f}%)" if view.latest_under_pct is not None else "  (n/a)")
    )
    typer.echo(
        "Contains estimates:  "
        + ("yes (back-filled days in this period)" if view.latest_contains_estimates else "no")
    )
    typer.echo()
    typer.echo(f"Alert level:         {view.alert.level}")
    accel_note = ""
    if view.alert.accelerating and view.alert.accelerating_mean is not None:
        accel_note = (
            f" ({view.alert.current_pct * 100:.3f}% is more than double the recent "
            f"{view.alert.accelerating_mean * 100:.3f}% mean)"
        )
    typer.echo(f"Accelerating:        {'yes' if view.alert.accelerating else 'no'}{accel_note}")
    if view.trend_direction:
        typer.echo(
            f"Trend:               {view.trend_direction} "
            f"(vs {view.trend_previous_pct * 100:.3f}% in {view.trend_previous_period})"
        )
    typer.echo()
    typer.echo(
        f"Last entry:          {view.last_entry_date}"
        + (f" ({view.days_since_last_entry} days ago)" if view.days_since_last_entry is not None else "")
    )
    if view.balance_lag_note:
        typer.echo(f"  Note: {view.balance_lag_note}")
    typer.echo()
    typer.echo(
        f"Validation:          {view.unresolved_block_count} unresolved BLOCK, "
        f"{view.warning_count} WARN since {view.latest_period_start} "
        "(run `gtp check` for detail)"
    )

    if write_alert_log(view, logged_at=datetime.now().isoformat(timespec="seconds")):
        typer.echo("\n(alert logged)")


@app.command()
def dashboard(
    port: int = typer.Option(8742, help="Local port to serve on."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser tab."),
) -> None:
    """Serve the local web dashboard (read-only) and open it in the browser."""
    from gtp.web.app import run

    typer.echo(f"Dashboard on http://127.0.0.1:{port}/ — Ctrl+C to stop.")
    run(port=port, open_browser=not no_browser)


@app.command()
def chart(
    out: Path = typer.Option(Path("out/trend.png"), "--out", help="PNG output path."),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database file."),
) -> None:
    """Render the FIT0101 under_pct trend chart."""
    conn = init_db(db_path)
    result_path = build_trend_chart(conn, out)
    typer.echo(f"Chart written to {result_path}")


if __name__ == "__main__":
    app()
