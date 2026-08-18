"""Water balance calculation per docs/DATA_SPEC.md §2.

calculate_balance() is a pure function: given two snapshots, the daily
readings between them, and a config, it computes a BalanceResult with no
database or file access. Everything below it (resolve_config,
find_bounding_snapshot, get_daily_readings, compute_period_balance) is
the orchestration layer that gathers those inputs from the database and
config.toml, and persists the result.
"""

import calendar
import sqlite3
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from gtp.models import BalanceResult, BalanceSnapshot, DailyReading

CONFIG_TOML_PATH = Path("config.toml")


@dataclass
class BalanceConfig:
    batch_volume_kl: float
    chem_base_litres_per_batch: float
    chem_acid_factor: float
    chem_ferrous_factor: float
    chem_h2o2_factor: float
    chem_caustic_factor: float


def calculate_balance(
    start_snapshot: BalanceSnapshot,
    end_snapshot: BalanceSnapshot,
    daily_readings: list[DailyReading],
    config: BalanceConfig,
) -> BalanceResult:
    missing = [
        r.date for r in daily_readings
        if r.fit0101_reading is None or r.fit0501 is None
    ]
    if missing:
        raise ValueError(
            "cannot compute a balance with unfilled data — missing "
            f"fit0101_reading/fit0501 for: {', '.join(missing)}"
        )

    total_in = sum(r.fit0101_reading - r.fit0101_error for r in daily_readings)
    total_out = sum(r.fit0501 for r in daily_readings)

    start_volume = start_snapshot.t02 + start_snapshot.t31 + start_snapshot.t32 + start_snapshot.t5 + start_snapshot.f4s
    end_volume = end_snapshot.t02 + end_snapshot.t31 + end_snapshot.t32 + end_snapshot.t5 + end_snapshot.f4s
    expected_end_volume = start_volume + (total_in - total_out)
    discrepancy = end_volume - expected_end_volume

    mains_used = end_snapshot.mains_meter - start_snapshot.mains_meter
    chem_litres_per_batch = config.chem_base_litres_per_batch * (
        config.chem_acid_factor
        + config.chem_ferrous_factor
        + config.chem_h2o2_factor
        + config.chem_caustic_factor
    )
    chemicals_used = (total_out / config.batch_volume_kl) * chem_litres_per_batch / 1000
    other = end_snapshot.other_adjustments

    under_kl = discrepancy - mains_used - chemicals_used - other
    under_pct = None if total_out == 0 else under_kl / total_out
    contains_estimates = int(any(r.is_estimated for r in daily_readings))

    return BalanceResult(
        period_start_snapshot_id=start_snapshot.id,
        period_end_snapshot_id=end_snapshot.id,
        period_start=start_snapshot.taken_at[:10],
        period_end=end_snapshot.taken_at[:10],
        total_in=total_in,
        total_out=total_out,
        start_volume=start_volume,
        end_volume=end_volume,
        mains_used=mains_used,
        chemicals_used=chemicals_used,
        other_adjustments=other,
        discrepancy_subtotal=discrepancy,
        fit0101_under_kl=under_kl,
        fit0101_under_pct=under_pct,
        batch_volume_kl=config.batch_volume_kl,
        chem_litres_per_batch=chem_litres_per_batch,
        computed_at=datetime.now().isoformat(timespec="seconds"),
        contains_estimates=contains_estimates,
    )


def load_config_from_toml() -> BalanceConfig:
    with open(CONFIG_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    chem = data["chemicals"]
    plant = data["plant"]
    return BalanceConfig(
        batch_volume_kl=plant["batch_volume_kl"],
        chem_base_litres_per_batch=chem["base_litres_per_batch"],
        chem_acid_factor=chem["acid_factor"],
        chem_ferrous_factor=chem["ferrous_factor"],
        chem_h2o2_factor=chem["h2o2_factor"],
        chem_caustic_factor=chem["caustic_factor"],
    )


def resolve_config(conn: sqlite3.Connection, as_of_date: str) -> BalanceConfig:
    """The config in force for a given date: the most recent config_history
    row at or before that date, or today's live config.toml if no
    historical record exists yet (and record it, so it's locked in).
    """
    row = conn.execute(
        """
        SELECT batch_volume_kl, chem_base_litres_per_batch, chem_acid_factor,
               chem_ferrous_factor, chem_h2o2_factor, chem_caustic_factor
        FROM config_history
        WHERE effective_from <= ?
        ORDER BY effective_from DESC
        LIMIT 1
        """,
        (as_of_date,),
    ).fetchone()
    if row is not None:
        return BalanceConfig(*row)

    config = load_config_from_toml()
    conn.execute(
        """
        INSERT INTO config_history
            (effective_from, batch_volume_kl, chem_base_litres_per_batch,
             chem_acid_factor, chem_ferrous_factor, chem_h2o2_factor,
             chem_caustic_factor, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(effective_from) DO NOTHING
        """,
        (
            as_of_date, config.batch_volume_kl, config.chem_base_litres_per_batch,
            config.chem_acid_factor, config.chem_ferrous_factor, config.chem_h2o2_factor,
            config.chem_caustic_factor, "config.toml",
        ),
    )
    conn.commit()
    return config


def find_bounding_snapshot(conn: sqlite3.Connection, date: str) -> BalanceSnapshot:
    """The latest balance_snapshot at or before the given date."""
    row = conn.execute(
        """
        SELECT id, taken_at, t02, t31, t32, t5, f4s, mains_meter,
               other_adjustments, other_note, note
        FROM balance_snapshot
        WHERE substr(taken_at, 1, 10) <= ?
        ORDER BY taken_at DESC
        LIMIT 1
        """,
        (date,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no balance_snapshot found at or before {date}")
    return BalanceSnapshot(
        id=row[0], taken_at=row[1], t02=row[2], t31=row[3], t32=row[4], t5=row[5],
        f4s=row[6], mains_meter=row[7], other_adjustments=row[8],
        other_note=row[9], note=row[10],
    )


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = []
    d = start
    while d <= end:
        dates.append(d.isoformat())
        d += timedelta(days=1)
    return dates


def get_daily_readings(
    conn: sqlite3.Connection, start_date: str, end_date: str
) -> list[DailyReading]:
    """All daily_reading rows in [start_date, end_date]. Raises if any date
    in the range has no row at all — per CLAUDE.md, missing data stays
    missing and is flagged, not silently skipped.
    """
    rows = conn.execute(
        """
        SELECT date, fit0101_reading, fit0101_error, fit0501, op_fraction,
               bore_status, comment, is_estimated, estimate_reason, entered_at
        FROM daily_reading
        WHERE date BETWEEN ? AND ?
        ORDER BY date
        """,
        (start_date, end_date),
    ).fetchall()

    found = {r[0] for r in rows}
    missing = [d for d in _date_range(start_date, end_date) if d not in found]
    if missing:
        shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
        raise ValueError(f"no daily_reading for: {shown}")

    return [
        DailyReading(
            date=r[0], fit0101_reading=r[1], fit0101_error=r[2] or 0.0, fit0501=r[3],
            op_fraction=r[4], bore_status=r[5], comment=r[6], is_estimated=r[7],
            estimate_reason=r[8], entered_at=r[9],
        )
        for r in rows
    ]


def _reported_mains_used_for_period(
    conn: sqlite3.Connection, start_snapshot: BalanceSnapshot, end_snapshot: BalanceSnapshot
) -> float | None:
    """A historical month's own directly-computed mains_used, if the given
    snapshots are exactly the pair that month's own sheet used.

    Needed because adjacent sheets occasionally disagree by ~0.1 kL about
    the mains reading at a shared boundary moment (confirmed at the
    May/Jun and Jul/Aug boundaries) — whichever sheet is imported last
    wins in the shared balance_snapshot table, which isn't necessarily
    the value a given month's own documented figure was computed from.
    Falls back to None (plain snapshot subtraction) for any period that
    doesn't line up with one specific historical month's own boundaries.
    """
    rows = conn.execute(
        "SELECT effective_from, mains_used_reported FROM config_history "
        "WHERE mains_used_reported IS NOT NULL"
    ).fetchall()
    for effective_from, mains_used_reported in rows:
        year, month = int(effective_from[:4]), int(effective_from[5:7])
        month_end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        try:
            expected_start = find_bounding_snapshot(conn, effective_from)
            expected_end = find_bounding_snapshot(conn, month_end)
        except ValueError:
            continue
        if (
            expected_start.taken_at == start_snapshot.taken_at
            and expected_end.taken_at == end_snapshot.taken_at
        ):
            return mains_used_reported
    return None


def _save_balance_result(conn: sqlite3.Connection, result: BalanceResult) -> None:
    conn.execute(
        """
        INSERT INTO balance_result
            (period_start_snapshot_id, period_end_snapshot_id, period_start,
             period_end, total_in, total_out, start_volume, end_volume,
             mains_used, chemicals_used, other_adjustments, discrepancy_subtotal,
             fit0101_under_kl, fit0101_under_pct, batch_volume_kl,
             chem_litres_per_batch, computed_at, contains_estimates)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(period_start, period_end) DO UPDATE SET
            period_start_snapshot_id = excluded.period_start_snapshot_id,
            period_end_snapshot_id   = excluded.period_end_snapshot_id,
            total_in                 = excluded.total_in,
            total_out                = excluded.total_out,
            start_volume             = excluded.start_volume,
            end_volume               = excluded.end_volume,
            mains_used               = excluded.mains_used,
            chemicals_used           = excluded.chemicals_used,
            other_adjustments        = excluded.other_adjustments,
            discrepancy_subtotal     = excluded.discrepancy_subtotal,
            fit0101_under_kl         = excluded.fit0101_under_kl,
            fit0101_under_pct        = excluded.fit0101_under_pct,
            batch_volume_kl          = excluded.batch_volume_kl,
            chem_litres_per_batch    = excluded.chem_litres_per_batch,
            computed_at              = excluded.computed_at,
            contains_estimates       = excluded.contains_estimates
        """,
        (
            result.period_start_snapshot_id, result.period_end_snapshot_id,
            result.period_start, result.period_end, result.total_in, result.total_out,
            result.start_volume, result.end_volume, result.mains_used,
            result.chemicals_used, result.other_adjustments, result.discrepancy_subtotal,
            result.fit0101_under_kl, result.fit0101_under_pct, result.batch_volume_kl,
            result.chem_litres_per_batch, result.computed_at, result.contains_estimates,
        ),
    )
    conn.commit()


def compute_period_balance(
    conn: sqlite3.Connection, start_date: str, end_date: str, persist: bool = True
) -> BalanceResult:
    """Resolve a period's snapshots, readings, and config, compute its
    balance, and (by default) save it.
    """
    start_snapshot = find_bounding_snapshot(conn, start_date)
    end_snapshot = find_bounding_snapshot(conn, end_date)
    daily_readings = get_daily_readings(conn, start_date, end_date)
    config = resolve_config(conn, end_date)

    reported_mains_used = _reported_mains_used_for_period(conn, start_snapshot, end_snapshot)
    if reported_mains_used is not None:
        end_snapshot = replace(
            end_snapshot, mains_meter=start_snapshot.mains_meter + reported_mains_used
        )

    result = calculate_balance(start_snapshot, end_snapshot, daily_readings, config)
    result.period_start = start_date
    result.period_end = end_date

    if persist:
        _save_balance_result(conn, result)

    return result
