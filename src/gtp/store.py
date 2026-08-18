"""Single place that writes a validated DailyReading/BalanceSnapshot to
the database. Shared by the CLI (gtp add/snapshot) and the GUI so both
interfaces save through identical SQL.
"""

import sqlite3
from datetime import datetime

from gtp.models import BalanceSnapshot, DailyReading


def save_daily_reading(
    conn: sqlite3.Connection, reading: DailyReading, override_reason: str | None
) -> None:
    conn.execute(
        """
        INSERT INTO daily_reading
            (date, fit0101_reading, fit0101_error, fit0501, op_fraction,
             bore_status, comment, is_estimated, estimate_reason, entered_at, override_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reading.date, reading.fit0101_reading, reading.fit0101_error,
            reading.fit0501, reading.op_fraction, reading.bore_status, reading.comment,
            reading.is_estimated, reading.estimate_reason,
            datetime.now().isoformat(timespec="seconds"), override_reason,
        ),
    )
    conn.commit()


def save_balance_snapshot(
    conn: sqlite3.Connection, snapshot: BalanceSnapshot, override_reason: str | None
) -> None:
    conn.execute(
        """
        INSERT INTO balance_snapshot
            (taken_at, t02, t31, t32, t5, f4s, mains_meter,
             other_adjustments, other_note, note, override_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.taken_at, snapshot.t02, snapshot.t31, snapshot.t32, snapshot.t5,
            snapshot.f4s, snapshot.mains_meter, snapshot.other_adjustments,
            snapshot.other_note, snapshot.note, override_reason,
        ),
    )
    conn.commit()
