"""Alert rules per docs/DATA_SPEC.md §5.

classify_level/accelerating_mean/is_accelerating/evaluate_alert are pure
functions -- no database access, no printing. trailing_periods_pct below
them is the one small database-touching helper, same split already used
in balance.py and validate.py.
"""

import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_TOML_PATH = Path("config.toml")


@dataclass
class AlertConfig:
    action_pct: float
    watch_pct: float
    accelerating_factor: float
    accelerating_window: int
    investigate_pct: float


def load_alert_config_from_toml() -> AlertConfig:
    with open(CONFIG_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    alerts = data["alerts"]
    return AlertConfig(
        action_pct=alerts["action_pct"],
        watch_pct=alerts["watch_pct"],
        accelerating_factor=alerts["accelerating_factor"],
        accelerating_window=alerts["accelerating_window"],
        investigate_pct=alerts["investigate_pct"],
    )


def classify_level(under_pct: float | None, config: AlertConfig) -> str:
    """under_pct is a fraction (e.g. 0.0092 for 0.922%); the config
    thresholds are written in percent, matching docs/DATA_SPEC.md §5 and
    config.toml's own comments -- convert before comparing.
    """
    if under_pct is None:
        return "NO_DATA"
    pct = under_pct * 100
    if pct > config.action_pct:
        return "ACTION"
    if pct > config.watch_pct:
        return "WATCH"
    if pct < config.investigate_pct:
        return "INVESTIGATE"
    return "OK"


def accelerating_mean(previous_pcts: list[float | None]) -> float | None:
    """Mean of the non-None values only. A period with no defined
    percentage (e.g. a zero-throughput month) isn't evidence of zero
    wear -- excluding it is more honest than guessing at a substitute.
    """
    usable = [p for p in previous_pcts if p is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def is_accelerating(
    current_pct: float | None, previous_pcts: list[float | None], config: AlertConfig
) -> bool:
    if current_pct is None:
        return False
    mean = accelerating_mean(previous_pcts)
    if mean is None or mean <= 0:
        return False
    return current_pct > config.accelerating_factor * mean


@dataclass
class AlertEvaluation:
    level: str
    accelerating: bool
    current_pct: float | None
    accelerating_mean: float | None


def evaluate_alert(
    current_pct: float | None, previous_pcts: list[float | None], config: AlertConfig
) -> AlertEvaluation:
    return AlertEvaluation(
        level=classify_level(current_pct, config),
        accelerating=is_accelerating(current_pct, previous_pcts, config),
        current_pct=current_pct,
        accelerating_mean=accelerating_mean(previous_pcts),
    )


def trailing_periods_pct(
    conn: sqlite3.Connection, before_period_start: str, window: int
) -> list[float | None]:
    """The `window` balance_result rows immediately before
    before_period_start, ascending by period_start.
    """
    rows = conn.execute(
        "SELECT fit0101_under_pct FROM balance_result WHERE period_start < ? "
        "ORDER BY period_start DESC LIMIT ?",
        (before_period_start, window),
    ).fetchall()
    return [r[0] for r in reversed(rows)]
