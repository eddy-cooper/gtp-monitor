"""Plain dataclasses mirroring the tables in gtp.db. No ORM, no DB logic here."""

from dataclasses import dataclass


@dataclass
class DailyReading:
    date: str
    fit0101_reading: float | None
    fit0101_error: float = 0.0
    fit0501: float | None = None
    op_fraction: float | None = None
    bore_status: str | None = None
    comment: str | None = None
    is_estimated: int = 0
    estimate_reason: str | None = None
    entered_at: str | None = None
    override_reason: str | None = None


@dataclass
class BalanceSnapshot:
    taken_at: str
    t02: float | None = None
    t31: float | None = None
    t32: float | None = None
    t5: float | None = None
    f4s: float = 0.0
    mains_meter: float | None = None
    other_adjustments: float = 0.0
    other_note: str | None = None
    note: str | None = None
    id: int | None = None
    override_reason: str | None = None


@dataclass
class BalanceResult:
    period_start_snapshot_id: int
    period_end_snapshot_id: int
    period_start: str
    period_end: str
    total_in: float
    total_out: float
    start_volume: float
    end_volume: float
    mains_used: float
    chemicals_used: float
    other_adjustments: float
    discrepancy_subtotal: float
    fit0101_under_kl: float
    fit0101_under_pct: float | None
    batch_volume_kl: float
    chem_litres_per_batch: float
    computed_at: str
    contains_estimates: int = 0
    id: int | None = None


@dataclass
class ServiceEvent:
    date: str
    item: str
    note: str | None = None
    id: int | None = None


@dataclass
class LegacyBalanceNote:
    sheet: str
    row: int
    label: str | None
    kl: float | None
    pct: float | None
    id: int | None = None
