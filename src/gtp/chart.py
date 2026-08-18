"""Trend chart generation (under_pct over time) per docs/DATA_SPEC.md §5.

Colors are taken from the project's validated data-viz palette (blue for
the single measured series, the reserved status reds/ambers for the
action/watch threshold lines) rather than picked arbitrarily.
"""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D

from gtp.alerts import AlertConfig, load_alert_config_from_toml

COLOR_SERIES = "#2a78d6"
COLOR_ACTION = "#d03b3b"
COLOR_WATCH = "#fab219"
COLOR_GRIDLINE = "#e1e0d9"
COLOR_AXIS = "#c3c2b7"
COLOR_TEXT_PRIMARY = "#0b0b0b"
COLOR_TEXT_SECONDARY = "#52514e"
COLOR_SURFACE = "#fcfcfb"


@dataclass
class TrendPoint:
    period_start: str
    period_end: str
    label: str
    under_pct: float | None
    contains_estimates: bool


def _format_label(period_start: str, period_end: str) -> str:
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    month_label = start.strftime("%b %Y")
    last_day = calendar.monthrange(start.year, start.month)[1]
    if end.day != last_day:
        return f"{month_label} ({start.day}–{end.day})"
    return month_label


def fetch_trend_points(conn: sqlite3.Connection) -> list[TrendPoint]:
    rows = conn.execute(
        "SELECT period_start, period_end, fit0101_under_pct, contains_estimates "
        "FROM balance_result ORDER BY period_start"
    ).fetchall()
    return [
        TrendPoint(
            period_start=r[0],
            period_end=r[1],
            label=_format_label(r[0], r[1]),
            under_pct=r[2],
            contains_estimates=bool(r[3]),
        )
        for r in rows
    ]


def render_trend_chart(
    points: list[TrendPoint], action_pct: float, watch_pct: float, out_path: str | Path,
    dpi: int = 150,
) -> Path:
    """Pure-ish: no database access, just matplotlib and a file write.

    dpi controls the output pixel size (the figure is 10x5.5 inches, so
    150 dpi = 1500x825 px). The GUI passes a smaller value so the PNG
    fits its window; the CLI keeps the full-resolution default.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor=COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    x = list(range(len(points)))
    labels = [p.label for p in points]
    # None (e.g. May, zero throughput) becomes NaN so matplotlib draws a
    # genuine gap in the line rather than a value that was never measured.
    y = [p.under_pct * 100 if p.under_pct is not None else float("nan") for p in points]

    ax.plot(x, y, color=COLOR_SERIES, linewidth=2, zorder=3)

    for xi, p in zip(x, points):
        if p.under_pct is None:
            continue
        yi = p.under_pct * 100
        if p.contains_estimates:
            # hollow marker: period contains back-filled/estimated data.
            # Shape, not a second color, so it reads in black & white too.
            ax.plot(
                xi, yi, marker="o", markersize=8, markerfacecolor=COLOR_SURFACE,
                markeredgecolor=COLOR_SERIES, markeredgewidth=2, linestyle="none", zorder=4,
            )
        else:
            ax.plot(
                xi, yi, marker="o", markersize=8, markerfacecolor=COLOR_SERIES,
                markeredgecolor=COLOR_SERIES, linestyle="none", zorder=4,
            )

    if points:
        right_edge = len(points) - 0.5
        ax.axhline(action_pct, color=COLOR_ACTION, linestyle="--", linewidth=1.5, zorder=2)
        ax.text(
            right_edge, action_pct, f" {action_pct:.1f}% action", color=COLOR_ACTION,
            va="bottom", ha="right", fontsize=9,
        )
        ax.axhline(watch_pct, color=COLOR_WATCH, linestyle="--", linewidth=1.5, zorder=2)
        ax.text(
            right_edge, watch_pct, f" {watch_pct:.1f}% watch", color=COLOR_WATCH,
            va="bottom", ha="right", fontsize=9,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right", color=COLOR_TEXT_SECONDARY, fontsize=9)
    ax.set_ylabel("FIT0101 under-reading (%)", color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_title("FIT0101 under-reading trend", color=COLOR_TEXT_PRIMARY, fontsize=13, loc="left")

    ax.grid(True, axis="y", color=COLOR_GRIDLINE, linewidth=0.8, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(COLOR_AXIS)
    ax.spines["bottom"].set_color(COLOR_AXIS)
    ax.tick_params(colors=COLOR_AXIS)

    if any(p.contains_estimates for p in points):
        handle = Line2D(
            [0], [0], marker="o", markersize=8, markerfacecolor=COLOR_SURFACE,
            markeredgecolor=COLOR_SERIES, markeredgewidth=2, linestyle="none",
            color=COLOR_SERIES, label="contains estimated data",
        )
        ax.legend(handles=[handle], loc="lower right", frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return out_path


def build_trend_chart(
    conn: sqlite3.Connection, out_path: str | Path, alert_config: AlertConfig | None = None,
    dpi: int = 150,
) -> Path:
    """Orchestrator: pull every calculated period and render the chart."""
    points = fetch_trend_points(conn)
    config = alert_config or load_alert_config_from_toml()
    return render_trend_chart(points, config.action_pct, config.watch_pct, out_path, dpi=dpi)
