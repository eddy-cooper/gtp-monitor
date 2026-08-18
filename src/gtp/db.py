"""SQLite schema creation and connection handling.

Schema per docs/DATA_SPEC.md Â§1, plus legacy_balance_note which
docs/MIGRATION.md asks for to hold non-reproducible reference figures
from the old workbook's weekly summary rows.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/gtp.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_reading (
    date            TEXT PRIMARY KEY,
    fit0101_reading REAL,
    fit0101_error   REAL DEFAULT 0,
    fit0501         REAL,
    op_fraction     REAL,
    bore_status     TEXT,
    comment         TEXT,
    is_estimated    INTEGER NOT NULL DEFAULT 0,
    estimate_reason TEXT,
    entered_at      TEXT,
    override_reason TEXT
);

CREATE TABLE IF NOT EXISTS balance_snapshot (
    id                 INTEGER PRIMARY KEY,
    taken_at           TEXT NOT NULL UNIQUE,
    t02                REAL,
    t31                REAL,
    t32                REAL,
    t5                 REAL,
    f4s                REAL DEFAULT 0,
    mains_meter        REAL,
    other_adjustments  REAL DEFAULT 0,
    other_note         TEXT,
    note               TEXT,
    override_reason    TEXT
);

CREATE TABLE IF NOT EXISTS balance_result (
    id                        INTEGER PRIMARY KEY,
    period_start_snapshot_id  INTEGER REFERENCES balance_snapshot(id),
    period_end_snapshot_id    INTEGER REFERENCES balance_snapshot(id),
    period_start              TEXT,
    period_end                TEXT,
    total_in                  REAL,
    total_out                 REAL,
    start_volume              REAL,
    end_volume                REAL,
    mains_used                REAL,
    chemicals_used            REAL,
    other_adjustments         REAL,
    discrepancy_subtotal      REAL,
    fit0101_under_kl          REAL,
    fit0101_under_pct         REAL,
    batch_volume_kl           REAL,
    chem_litres_per_batch     REAL,
    computed_at               TEXT,
    contains_estimates        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (period_start, period_end)
);

CREATE TABLE IF NOT EXISTS service_event (
    id                INTEGER PRIMARY KEY,
    date              TEXT,
    item              TEXT,
    note              TEXT,
    hours_at_service  REAL,
    interval_hours    REAL
);

CREATE TABLE IF NOT EXISTS legacy_balance_note (
    id     INTEGER PRIMARY KEY,
    sheet  TEXT,
    row    INTEGER,
    label  TEXT,
    kl     REAL,
    pct    REAL
);

CREATE TABLE IF NOT EXISTS config_history (
    id                          INTEGER PRIMARY KEY,
    effective_from              TEXT NOT NULL,
    batch_volume_kl             REAL,
    chem_base_litres_per_batch  REAL,
    chem_acid_factor            REAL,
    chem_ferrous_factor         REAL,
    chem_h2o2_factor            REAL,
    chem_caustic_factor         REAL,
    mains_used_reported         REAL,
    source                      TEXT,
    UNIQUE (effective_from)
);
"""

_BALANCE_RESULT_UNIQUE_MARKER = "UNIQUE (period_start, period_end)"


def _migrate_balance_result(conn: sqlite3.Connection) -> None:
    """Phase 1 created balance_result without a uniqueness rule on
    (period_start, period_end). Phase 2 needs one so gtp balance can
    update an existing period's result instead of duplicating it. The
    table has been empty since Phase 1, so it's safe to drop and let the
    schema script below recreate it with the constraint.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'balance_result'"
    ).fetchone()
    if row is None or _BALANCE_RESULT_UNIQUE_MARKER in (row[0] or ""):
        return  # doesn't exist yet, or already migrated
    (count,) = conn.execute("SELECT COUNT(*) FROM balance_result").fetchone()
    if count:
        raise RuntimeError(
            "balance_result has rows but predates the (period_start, period_end) "
            "uniqueness rule; refusing to drop it automatically."
        )
    conn.execute("DROP TABLE balance_result")


def _migrate_config_history(conn: sqlite3.Connection) -> None:
    """config_history is entirely derived from the read-only workbook
    (re-running gtp import fully repopulates it), so it's safe to drop
    and recreate whenever its shape is out of date.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(config_history)")}
    if not cols or "mains_used_reported" in cols:
        return  # doesn't exist yet, or already migrated
    conn.execute("DROP TABLE config_history")


def _migrate_override_reason_columns(conn: sqlite3.Connection) -> None:
    """daily_reading and balance_snapshot hold real imported history
    (unlike balance_result/config_history above), so this migration must
    only ever ADD a column, never drop or recreate either table.
    """
    for table in ("daily_reading", "balance_snapshot"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if cols and "override_reason" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN override_reason TEXT")


def _migrate_balance_result_contains_estimates(conn: sqlite3.Connection) -> None:
    """balance_result already holds real calculated history (unlike the
    empty/re-derivable tables above), so -- same reasoning as
    _migrate_override_reason_columns -- this must only ever ADD a column,
    never drop or recreate. A plain ADD COLUMN would default every
    existing row to 0 (no estimates), which is wrong for periods that
    genuinely do contain back-filled days (e.g. PLC-freeze
    dates), so this backfills the correct value from daily_reading's
    already-populated is_estimated flag -- not inventing anything, just
    re-deriving it from data that's already there.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(balance_result)")}
    if not cols or "contains_estimates" in cols:
        return  # doesn't exist yet, or already migrated
    conn.execute(
        "ALTER TABLE balance_result ADD COLUMN contains_estimates INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        """
        UPDATE balance_result SET contains_estimates = (
            SELECT COUNT(*) > 0 FROM daily_reading dr
            WHERE dr.date BETWEEN balance_result.period_start AND balance_result.period_end
              AND dr.is_estimated = 1
        )
        """
    )


def _migrate_service_event_hours_columns(conn: sqlite3.Connection) -> None:
    """service_event holds imported history, so -- same reasoning as
    _migrate_override_reason_columns -- only ever ADD columns, never drop
    or recreate. NULL means date-based servicing; equipment serviced on
    running hours (docs/OPERATIONS.md) fills these in.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(service_event)")}
    if not cols:
        return  # doesn't exist yet; schema script creates it fully formed
    for column in ("hours_at_service", "interval_hours"):
        if column not in cols:
            conn.execute(f"ALTER TABLE service_event ADD COLUMN {column} REAL")


def init_db(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create the database file and tables if they don't already exist.

    Safe to call every time the tool starts.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_balance_result(conn)
    _migrate_config_history(conn)
    _migrate_override_reason_columns(conn)
    _migrate_balance_result_contains_estimates(conn)
    _migrate_service_event_hours_columns(conn)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn
