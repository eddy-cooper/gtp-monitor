"""One-command demo setup: generate the synthetic workbook, import it,
compute every period's balance, and stage the one deliberate data-entry
mistake the README demonstrates the override workflow on.

Usage (from the project root, venv active):

    python tools/setup_demo.py

Refuses to run if data/gtp.db already exists -- this script is for a
fresh clone, and must never write demo rows into a database that
already holds real entries.
"""

import calendar
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_demo_data as mdd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gtp.db"


def build_demo_db(db_path: Path, workbook_path: Path):
    """Import the demo workbook and compute all balances. Returns the
    ImportSummary. Kept as a plain function so tests can drive it against
    a temporary database.
    """
    from gtp.balance import compute_period_balance
    from gtp.importer import import_workbook
    from gtp.models import BalanceSnapshot
    from gtp.store import save_balance_snapshot

    summary = import_workbook(workbook_path, db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for spec in mdd.MONTHS:
        last = spec.days_filled or calendar.monthrange(spec.year, spec.month)[1]
        start = f"{spec.year:04d}-{spec.month:02d}-01"
        end = f"{spec.year:04d}-{spec.month:02d}-{last:02d}"
        compute_period_balance(conn, start, end)

    # The staged mistake: a mains meter read mis-keyed by +100 kL,
    # written through the store layer (no validation) the way rows that
    # predate validation entered the real schema. `gtp check` reports it
    # as an unresolved V9 BLOCK until `gtp override` records a reason.
    tanks = conn.execute(
        "SELECT t02, t31, t32, t5 FROM balance_snapshot ORDER BY taken_at DESC LIMIT 1"
    ).fetchone()
    save_balance_snapshot(
        conn,
        BalanceSnapshot(
            taken_at=mdd.TYPO_SNAPSHOT["taken_at"],
            t02=tanks[0], t31=tanks[1], t32=tanks[2], t5=tanks[3], f4s=0.0,
            mains_meter=mdd.TYPO_SNAPSHOT["mains_meter"],
            note=mdd.TYPO_SNAPSHOT["note"],
        ),
        override_reason=None,
    )
    conn.close()
    return summary


def main() -> None:
    if DEFAULT_DB.exists():
        print(
            f"Refusing to run: {DEFAULT_DB} already exists.\n"
            "This script sets up a fresh demo database only. Delete the file "
            "first if you really want to regenerate the demo."
        )
        raise SystemExit(1)

    mdd.main()
    summary = build_demo_db(DEFAULT_DB, mdd.WORKBOOK_PATH)
    print()
    print(f"Imported {summary.sheets_imported} sheets, {summary.days_imported} day rows.")
    if summary.warnings:
        print(f"Import warnings ({len(summary.warnings)}):")
        for w in summary.warnings:
            print(f"  - {w}")
    print()
    print("Demo ready. Try:")
    print("  gtp status")
    print("  gtp check --since 2026-06-01")
    print("  gtp dashboard        (or double-click gtp_dashboard.pyw)")


if __name__ == "__main__":
    main()
