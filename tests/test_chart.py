"""Tests for the trend chart, per docs/DATA_SPEC.md §5."""

import sqlite3
from pathlib import Path

from gtp.chart import TrendPoint, build_trend_chart, fetch_trend_points, render_trend_chart
from gtp.db import init_db
from gtp.importer import import_workbook

DEMO_WORKBOOK = (
    Path(__file__).resolve().parent.parent / "demo" / "Demo Operational Record.xlsx"
)


def _seed_balance_result(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    for period_start, period_end, under_pct, contains_estimates in rows:
        conn.execute(
            "INSERT INTO balance_result (period_start, period_end, fit0101_under_pct, "
            "contains_estimates) VALUES (?, ?, ?, ?)",
            (period_start, period_end, under_pct, contains_estimates),
        )
    conn.commit()


def test_fetch_trend_points_orders_and_labels_correctly(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    _seed_balance_result(conn, [
        ("2026-07-01", "2026-07-31", 0.00512, 1),
        ("2025-11-01", "2025-11-30", 0.00181, 0),
        ("2026-05-01", "2026-05-31", None, 0),
    ])

    points = fetch_trend_points(conn)
    assert [p.period_start for p in points] == ["2025-11-01", "2026-05-01", "2026-07-01"]
    assert points[0].label == "Nov 2025"
    assert points[1].under_pct is None
    assert points[2].contains_estimates is True


def test_fetch_trend_points_labels_partial_month(tmp_path: Path):
    conn = init_db(tmp_path / "gtp.db")
    _seed_balance_result(conn, [("2026-08-01", "2026-08-14", 0.0093, 1)])
    points = fetch_trend_points(conn)
    assert "1" in points[0].label and "14" in points[0].label
    assert "Aug 2026" in points[0].label


def test_render_trend_chart_creates_file(tmp_path: Path):
    points = [
        TrendPoint("2025-11-01", "2025-11-30", "Nov 2025", 0.00181, False),
        TrendPoint("2026-05-01", "2026-05-31", "May 2026", None, False),
        TrendPoint("2026-07-01", "2026-07-31", "Jul 2026", 0.00512, True),
    ]
    out_path = tmp_path / "trend.png"
    result = render_trend_chart(points, action_pct=1.0, watch_pct=0.5, out_path=out_path)
    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_render_trend_chart_creates_parent_directory(tmp_path: Path):
    out_path = tmp_path / "nested" / "dir" / "trend.png"
    points = [TrendPoint("2026-07-01", "2026-07-31", "Jul 2026", 0.005, False)]
    render_trend_chart(points, 1.0, 0.5, out_path)
    assert out_path.exists()


def test_render_trend_chart_handles_all_none_points(tmp_path: Path):
    points = [
        TrendPoint("2026-05-01", "2026-05-31", "May 2026", None, False),
        TrendPoint("2026-06-01", "2026-06-30", "Jun 2026", None, False),
    ]
    out_path = tmp_path / "trend.png"
    render_trend_chart(points, 1.0, 0.5, out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def _png_size(path: Path) -> tuple[int, int]:
    # PNG header: bytes 16-24 of the file are the IHDR width/height,
    # big-endian -- enough to check pixel dimensions without adding an
    # image library to the stack.
    data = path.read_bytes()
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_render_trend_chart_dpi_controls_pixel_size(tmp_path: Path):
    points = [TrendPoint("2026-07-01", "2026-07-31", "Jul 2026", 0.005, False)]
    full = tmp_path / "full.png"
    half = tmp_path / "half.png"
    render_trend_chart(points, 1.0, 0.5, full)          # default 150 dpi
    render_trend_chart(points, 1.0, 0.5, half, dpi=75)  # half resolution

    assert _png_size(full) == (1500, 825)  # 10x5.5 inches at 150 dpi
    assert _png_size(half) == (750, 412)   # same figure at 75 dpi


def test_render_trend_chart_handles_single_point(tmp_path: Path):
    points = [TrendPoint("2026-07-01", "2026-07-31", "Jul 2026", 0.005, False)]
    out_path = tmp_path / "trend.png"
    render_trend_chart(points, 1.0, 0.5, out_path)
    assert out_path.exists()


def test_build_trend_chart_end_to_end_with_demo_data(tmp_path: Path):
    db_path = tmp_path / "gtp.db"
    import_workbook(DEMO_WORKBOOK, db_path)
    conn = sqlite3.connect(db_path)

    from gtp.balance import compute_period_balance

    for start, end in [
        ("2025-10-01", "2025-10-31"), ("2025-11-01", "2025-11-30"),
        ("2025-12-01", "2025-12-31"), ("2026-01-01", "2026-01-31"),
        ("2026-02-01", "2026-02-28"), ("2026-03-01", "2026-03-31"),
        ("2026-04-01", "2026-04-30"), ("2026-05-01", "2026-05-31"),
        ("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31"),
        ("2026-08-01", "2026-08-14"),
    ]:
        compute_period_balance(conn, start, end, persist=True)

    out_path = tmp_path / "trend.png"
    result = build_trend_chart(conn, out_path)
    assert result.exists()
    assert result.stat().st_size > 0

    points = fetch_trend_points(conn)
    assert len(points) == 11
    assert points[-1].contains_estimates is True  # August's back-filled day
    assert any(p.under_pct is None for p in points)  # February offline
