"""Validation rules per docs/DATA_SPEC.md §3.

Each rule is a small pure function: no database access, no printing.
validate_daily_reading/validate_balance_snapshot are the "candidate entry
plus recent history" aggregators docs/BUILD_PLAN.md describes. Everything
below the aggregators (recent_daily_readings, recent_balance_snapshots,
run_check) is the database-touching orchestration layer, kept in this
same file deliberately -- mirroring the pure/orchestration split already
established in balance.py.
"""

import statistics
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from gtp.models import BalanceSnapshot, DailyReading

CONFIG_TOML_PATH = Path("config.toml")

BLOCK = "BLOCK"
WARN = "WARN"


@dataclass(frozen=True)
class Finding:
    rule: str          # "V1".."V13"
    level: str          # BLOCK or WARN
    entity: str          # "daily_reading" or "balance_snapshot"
    subject: str          # date for daily_reading, taken_at for balance_snapshot
    message: str


@dataclass
class ValidationConfig:
    max_daily_kl: float
    typical_daily_min_kl: float
    typical_daily_max_kl: float
    consecutive_freeze_days: int
    mains_increase_factor: float
    mains_median_window: int
    daily_imbalance_kl: float
    tank_capacities_kl: dict[str, float] | None = None


def load_validation_config_from_toml() -> ValidationConfig:
    with open(CONFIG_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    plant = data["plant"]
    validation = data["validation"]
    return ValidationConfig(
        max_daily_kl=plant["max_daily_kl"],
        typical_daily_min_kl=plant["typical_daily_min_kl"],
        typical_daily_max_kl=plant["typical_daily_max_kl"],
        consecutive_freeze_days=validation["consecutive_freeze_days"],
        mains_increase_factor=validation["mains_increase_factor"],
        mains_median_window=validation["mains_median_window"],
        daily_imbalance_kl=validation["daily_imbalance_kl"],
        tank_capacities_kl=None,
    )


def _fit0101_actual(candidate: DailyReading) -> float | None:
    if candidate.fit0101_reading is None:
        return None
    return candidate.fit0101_reading - candidate.fit0101_error


# --- daily_reading rules -------------------------------------------------


def check_v1_negative_flow(candidate: DailyReading) -> list[Finding]:
    findings = []
    actual = _fit0101_actual(candidate)
    if actual is not None and actual < 0:
        findings.append(Finding("V1", BLOCK, "daily_reading", candidate.date,
                                 f"fit0101_actual ({actual}) is negative."))
    if candidate.fit0501 is not None and candidate.fit0501 < 0:
        findings.append(Finding("V1", BLOCK, "daily_reading", candidate.date,
                                 f"fit0501 ({candidate.fit0501}) is negative."))
    return findings


def check_v2_exceeds_max_daily(candidate: DailyReading, config: ValidationConfig) -> list[Finding]:
    findings = []
    for name, value in (("fit0101_actual", _fit0101_actual(candidate)), ("fit0501", candidate.fit0501)):
        if value is not None and value > config.max_daily_kl:
            findings.append(Finding("V2", BLOCK, "daily_reading", candidate.date,
                                     f"{name} ({value}) exceeds max_daily_kl ({config.max_daily_kl})."))
    return findings


def check_v3_exceeds_typical_daily(candidate: DailyReading, config: ValidationConfig) -> list[Finding]:
    findings = []
    for name, value in (("fit0101_actual", _fit0101_actual(candidate)), ("fit0501", candidate.fit0501)):
        if value is not None and config.typical_daily_max_kl < value <= config.max_daily_kl:
            findings.append(Finding("V3", WARN, "daily_reading", candidate.date,
                                     f"{name} ({value}) exceeds typical_daily_max_kl ({config.typical_daily_max_kl})."))
    return findings


def check_v4_below_typical_min(candidate: DailyReading, config: ValidationConfig) -> list[Finding]:
    if candidate.op_fraction != 1.0:
        return []
    findings = []
    for name, value in (("fit0101_actual", _fit0101_actual(candidate)), ("fit0501", candidate.fit0501)):
        if value is not None and value < config.typical_daily_min_kl:
            findings.append(Finding("V4", WARN, "daily_reading", candidate.date,
                                     f"{name} ({value}) is below typical_daily_min_kl "
                                     f"({config.typical_daily_min_kl}) while op_fraction is 1.0."))
    return findings


def check_v5_op_fraction_mismatch(candidate: DailyReading) -> list[Finding]:
    actual = _fit0101_actual(candidate)
    if actual is None or candidate.fit0501 is None or candidate.op_fraction is None:
        return []
    if candidate.op_fraction == 0.0 and (actual != 0 or candidate.fit0501 != 0):
        return [Finding("V5", WARN, "daily_reading", candidate.date,
                         "Non-zero flow recorded while op_fraction is 0.0.")]
    if candidate.op_fraction == 1.0 and actual == 0 and candidate.fit0501 == 0:
        return [Finding("V5", WARN, "daily_reading", candidate.date,
                         "Zero flow recorded while op_fraction is 1.0.")]
    return []


def check_v6_possible_plc_freeze(
    candidate: DailyReading, recent_readings: list[DailyReading], config: ValidationConfig
) -> list[Finding]:
    needed = config.consecutive_freeze_days - 1
    if needed <= 0 or len(recent_readings) < needed:
        return []
    tail = recent_readings[-needed:]
    dates = [date.fromisoformat(r.date) for r in tail] + [date.fromisoformat(candidate.date)]
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days != 1:
            return []
    if candidate.fit0101_reading is None or candidate.fit0501 is None:
        return []
    values = {(r.fit0101_reading, r.fit0501) for r in tail}
    values.add((candidate.fit0101_reading, candidate.fit0501))
    if len(values) == 1:
        return [Finding(
            "V6", WARN, "daily_reading", candidate.date,
            f"{config.consecutive_freeze_days}+ consecutive days with identical FIT0101/FIT0501 "
            f"readings ({candidate.fit0101_reading}, {candidate.fit0501}) ending {candidate.date} "
            "— possible PLC freeze.",
        )]
    return []


def _missing_dates_between(start: date, end: date) -> list[str]:
    missing = []
    d = start + timedelta(days=1)
    while d < end:
        missing.append(d.isoformat())
        d += timedelta(days=1)
    return missing


def check_v7_gap_since_last_entry(
    candidate: DailyReading, previous_reading: DailyReading | None
) -> list[Finding]:
    if previous_reading is None:
        return []
    prev_date = date.fromisoformat(previous_reading.date)
    cand_date = date.fromisoformat(candidate.date)
    if (cand_date - prev_date).days <= 1:
        return []
    missing = _missing_dates_between(prev_date, cand_date)
    shown = ", ".join(missing[:5])
    if len(missing) > 5:
        shown += f" ... and {len(missing) - 5} more"
    return [Finding("V7", WARN, "daily_reading", candidate.date,
                     f"Gap since last entry ({previous_reading.date}) — missing dates: {shown}.")]


def check_v11_estimated_without_reason(candidate: DailyReading) -> list[Finding]:
    if candidate.is_estimated and not candidate.estimate_reason:
        return [Finding("V11", BLOCK, "daily_reading", candidate.date,
                         "is_estimated is set but no estimate_reason was given.")]
    return []


def check_v12_daily_imbalance(candidate: DailyReading, config: ValidationConfig) -> list[Finding]:
    actual = _fit0101_actual(candidate)
    if actual is None or candidate.fit0501 is None:
        return []
    diff = abs(actual - candidate.fit0501)
    if diff > config.daily_imbalance_kl:
        return [Finding("V12", WARN, "daily_reading", candidate.date,
                         f"|in - out| = {diff:.2f} kL, more than the daily_imbalance_kl "
                         f"threshold ({config.daily_imbalance_kl} kL).")]
    return []


# --- balance_snapshot rules ------------------------------------------------


def check_v8_mains_meter_decreased(
    candidate: BalanceSnapshot, previous_snapshot: BalanceSnapshot | None
) -> list[Finding]:
    if previous_snapshot is None or candidate.mains_meter is None or previous_snapshot.mains_meter is None:
        return []
    if candidate.mains_meter < previous_snapshot.mains_meter:
        return [Finding("V8", BLOCK, "balance_snapshot", candidate.taken_at,
                         f"mains_meter ({candidate.mains_meter}) is lower than the previous "
                         f"snapshot ({previous_snapshot.mains_meter} at {previous_snapshot.taken_at}).")]
    return []


def check_v9_mains_meter_spike(
    candidate: BalanceSnapshot, recent_snapshots: list[BalanceSnapshot], config: ValidationConfig
) -> list[Finding]:
    if not recent_snapshots or candidate.mains_meter is None:
        return []
    window = recent_snapshots[-(config.mains_median_window + 1):]
    if len(window) < 2:
        return []
    intervals = [
        window[i + 1].mains_meter - window[i].mains_meter
        for i in range(len(window) - 1)
        if window[i + 1].mains_meter is not None and window[i].mains_meter is not None
    ]
    if not intervals:
        return []
    median_interval = statistics.median(intervals)
    previous = recent_snapshots[-1]
    if previous.mains_meter is None or median_interval <= 0:
        return []
    delta = candidate.mains_meter - previous.mains_meter
    if delta > config.mains_increase_factor * median_interval:
        return [Finding(
            "V9", BLOCK, "balance_snapshot", candidate.taken_at,
            f"mains_meter increased by {delta:.2f} kL since the previous snapshot "
            f"({previous.taken_at}), more than {config.mains_increase_factor}x the recent "
            f"typical interval ({median_interval:.2f} kL median of {len(intervals)} prior intervals).",
        )]
    return []


_CAPACITY_TANKS = ("t02", "t31", "t32", "t5")  # f4s excluded -- no documented capacity


def check_v10_tank_volume_out_of_range(
    candidate: BalanceSnapshot, config: ValidationConfig
) -> list[Finding]:
    findings = []
    tanks = {"t02": candidate.t02, "t31": candidate.t31, "t32": candidate.t32,
             "t5": candidate.t5, "f4s": candidate.f4s}
    for name, value in tanks.items():
        if value is not None and value < 0:
            findings.append(Finding("V10", BLOCK, "balance_snapshot", candidate.taken_at,
                                     f"{name} reading ({value}) is negative."))
    if config.tank_capacities_kl:
        for name in _CAPACITY_TANKS:
            capacity = config.tank_capacities_kl.get(name)
            value = tanks.get(name)
            if capacity is not None and value is not None and value > capacity:
                findings.append(Finding("V10", BLOCK, "balance_snapshot", candidate.taken_at,
                                         f"{name} reading ({value}) exceeds configured capacity "
                                         f"({capacity} kL)."))
    return findings


# --- shared ----------------------------------------------------------------


def check_v13_future_date(entity: str, subject_date: str, today: str) -> list[Finding]:
    # subject_date may be a plain date (daily_reading) or a full ISO
    # datetime (balance_snapshot) -- compare date portions only, since a
    # snapshot taken today would otherwise sort "after" a plain today date.
    if subject_date[:10] > today[:10]:
        return [Finding("V13", BLOCK, entity, subject_date,
                         f"date {subject_date} is in the future (today is {today}).")]
    return []


# --- aggregators -------------------------------------------------------------


def validate_daily_reading(
    candidate: DailyReading,
    recent_readings: list[DailyReading],
    config: ValidationConfig,
    today: str,
) -> list[Finding]:
    previous_reading = recent_readings[-1] if recent_readings else None
    findings: list[Finding] = []
    findings += check_v1_negative_flow(candidate)
    findings += check_v2_exceeds_max_daily(candidate, config)
    findings += check_v3_exceeds_typical_daily(candidate, config)
    findings += check_v4_below_typical_min(candidate, config)
    findings += check_v5_op_fraction_mismatch(candidate)
    findings += check_v6_possible_plc_freeze(candidate, recent_readings, config)
    findings += check_v7_gap_since_last_entry(candidate, previous_reading)
    findings += check_v11_estimated_without_reason(candidate)
    findings += check_v12_daily_imbalance(candidate, config)
    findings += check_v13_future_date("daily_reading", candidate.date, today)
    return findings


def validate_balance_snapshot(
    candidate: BalanceSnapshot,
    recent_snapshots: list[BalanceSnapshot],
    config: ValidationConfig,
    today: str,
) -> list[Finding]:
    previous_snapshot = recent_snapshots[-1] if recent_snapshots else None
    findings: list[Finding] = []
    findings += check_v8_mains_meter_decreased(candidate, previous_snapshot)
    findings += check_v9_mains_meter_spike(candidate, recent_snapshots, config)
    findings += check_v10_tank_volume_out_of_range(candidate, config)
    findings += check_v13_future_date("balance_snapshot", candidate.taken_at, today)
    return findings


# --- database orchestration ------------------------------------------------

_READING_COLUMNS = (
    "date, fit0101_reading, fit0101_error, fit0501, op_fraction, bore_status, "
    "comment, is_estimated, estimate_reason, entered_at, override_reason"
)
_SNAPSHOT_COLUMNS = (
    "id, taken_at, t02, t31, t32, t5, f4s, mains_meter, other_adjustments, "
    "other_note, note, override_reason"
)


def _row_to_reading(r) -> DailyReading:
    return DailyReading(
        date=r[0], fit0101_reading=r[1], fit0101_error=r[2] or 0.0, fit0501=r[3],
        op_fraction=r[4], bore_status=r[5], comment=r[6], is_estimated=r[7],
        estimate_reason=r[8], entered_at=r[9], override_reason=r[10],
    )


def _row_to_snapshot(r) -> BalanceSnapshot:
    return BalanceSnapshot(
        id=r[0], taken_at=r[1], t02=r[2], t31=r[3], t32=r[4], t5=r[5], f4s=r[6],
        mains_meter=r[7], other_adjustments=r[8], other_note=r[9], note=r[10],
        override_reason=r[11],
    )


def recent_daily_readings(conn, before_date: str, limit: int = 14) -> list[DailyReading]:
    """The most recent daily_reading rows strictly before before_date,
    ascending by date -- the "recent history" a new candidate needs.
    """
    rows = conn.execute(
        f"SELECT {_READING_COLUMNS} FROM daily_reading WHERE date < ? "
        "ORDER BY date DESC LIMIT ?",
        (before_date, limit),
    ).fetchall()
    return [_row_to_reading(r) for r in reversed(rows)]


def recent_balance_snapshots(conn, before_taken_at: str, limit: int) -> list[BalanceSnapshot]:
    rows = conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM balance_snapshot WHERE taken_at < ? "
        "ORDER BY taken_at DESC LIMIT ?",
        (before_taken_at, limit),
    ).fetchall()
    return [_row_to_snapshot(r) for r in reversed(rows)]


@dataclass
class CheckReport:
    scanned_readings: int
    skipped_readings: int
    scanned_snapshots: int
    unresolved_blocks: list[Finding]
    overridden_blocks: list[Finding]
    warnings: list[Finding]
    estimated_entries: list[tuple[str, str]]


def run_check(
    conn, config: ValidationConfig | None = None, today: str | None = None
) -> CheckReport:
    """Re-run every rule against all existing data. Read-only."""
    if config is None:
        config = load_validation_config_from_toml()
    if today is None:
        today = date.today().isoformat()

    unresolved_blocks: list[Finding] = []
    overridden_blocks: list[Finding] = []
    warnings: list[Finding] = []

    def _classify(findings: list[Finding], override_reason: str | None) -> None:
        for f in findings:
            if f.level == BLOCK:
                (overridden_blocks if override_reason else unresolved_blocks).append(f)
            else:
                warnings.append(f)

    scanned_readings = 0
    skipped_readings = 0
    window: list[DailyReading] = []
    for r in conn.execute(f"SELECT {_READING_COLUMNS} FROM daily_reading ORDER BY date"):
        if r[1] is None and r[3] is None:
            skipped_readings += 1
            continue
        candidate = _row_to_reading(r)
        scanned_readings += 1
        _classify(validate_daily_reading(candidate, window, config, today), candidate.override_reason)
        window.append(candidate)
        if len(window) > 14:
            window.pop(0)

    scanned_snapshots = 0
    snap_window: list[BalanceSnapshot] = []
    snap_window_size = config.mains_median_window + 1
    for r in conn.execute(f"SELECT {_SNAPSHOT_COLUMNS} FROM balance_snapshot ORDER BY taken_at"):
        candidate = _row_to_snapshot(r)
        scanned_snapshots += 1
        _classify(validate_balance_snapshot(candidate, snap_window, config, today), candidate.override_reason)
        snap_window.append(candidate)
        if len(snap_window) > snap_window_size:
            snap_window.pop(0)

    estimated_entries = [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT date, estimate_reason FROM daily_reading WHERE is_estimated = 1 ORDER BY date"
        )
    ]

    return CheckReport(
        scanned_readings=scanned_readings,
        skipped_readings=skipped_readings,
        scanned_snapshots=scanned_snapshots,
        unresolved_blocks=unresolved_blocks,
        overridden_blocks=overridden_blocks,
        warnings=warnings,
        estimated_entries=estimated_entries,
    )
