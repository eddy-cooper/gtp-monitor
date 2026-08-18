"""Tests for the alert rules, per docs/DATA_SPEC.md §5.

The real August/May numbers are used as a permanent regression check: if
a future change ever makes August wrongly read ACTION again, or makes
the accelerating rule stop firing, this test catches it immediately.
"""

import pytest

from gtp.alerts import (
    AlertConfig,
    accelerating_mean,
    classify_level,
    evaluate_alert,
    is_accelerating,
    load_alert_config_from_toml,
)

CONFIG = AlertConfig(
    action_pct=1.0,
    watch_pct=0.5,
    accelerating_factor=2.0,
    accelerating_window=3,
    investigate_pct=-0.5,
)


# --- classify_level ---------------------------------------------------


def test_classify_level_action():
    assert classify_level(0.015, CONFIG) == "ACTION"


def test_classify_level_watch():
    assert classify_level(0.007, CONFIG) == "WATCH"


def test_classify_level_ok():
    assert classify_level(0.003, CONFIG) == "OK"


def test_classify_level_investigate():
    assert classify_level(-0.006, CONFIG) == "INVESTIGATE"


def test_classify_level_none_is_no_data():
    assert classify_level(None, CONFIG) == "NO_DATA"


def test_classify_level_exactly_at_action_threshold_is_watch_not_action():
    """Spec says "> 1.0%", not ">=" -- exactly 1.0% doesn't cross into ACTION."""
    assert classify_level(0.01, CONFIG) == "WATCH"


def test_classify_level_exactly_at_watch_threshold_is_ok():
    assert classify_level(0.005, CONFIG) == "OK"


def test_classify_level_exactly_at_investigate_threshold_is_ok():
    """Spec says "beyond -0.5%", not "at or beyond" -- exactly -0.5% is still OK."""
    assert classify_level(-0.005, CONFIG) == "OK"


# --- accelerating_mean --------------------------------------------------


def test_accelerating_mean_excludes_none():
    assert accelerating_mean([0.005, 0.003, None]) == pytest.approx(0.004)


def test_accelerating_mean_all_none_returns_none():
    assert accelerating_mean([None, None, None]) is None


def test_accelerating_mean_empty_returns_none():
    assert accelerating_mean([]) is None


# --- is_accelerating / evaluate_alert -----------------------------------


def test_is_accelerating_true_when_more_than_double_mean():
    assert is_accelerating(0.009, [0.005, 0.003, None], CONFIG) is True


def test_is_accelerating_false_when_not_double():
    assert is_accelerating(0.005, [0.005, 0.003, None], CONFIG) is False


def test_is_accelerating_false_when_current_is_none():
    assert is_accelerating(None, [0.005, 0.003], CONFIG) is False


def test_is_accelerating_false_when_all_previous_are_none():
    assert is_accelerating(0.009, [None, None, None], CONFIG) is False


def test_is_accelerating_false_when_mean_is_zero_or_negative():
    assert is_accelerating(0.001, [0.0, -0.001], CONFIG) is False


def test_evaluate_alert_combines_level_and_accelerating():
    result = evaluate_alert(0.009, [0.005, 0.003, None], CONFIG)
    assert result.level == "WATCH"
    assert result.accelerating is True
    assert result.current_pct == pytest.approx(0.009)
    assert result.accelerating_mean == pytest.approx(0.004)


# --- regression test using the demo dataset's documented numbers ---------


def test_demo_august_reads_watch_not_action_and_is_accelerating():
    """Literal constants from the demo story (docs/DATA_SPEC.md §4):
    the final accelerating period must classify WATCH — above the 0.5%
    watch line, still short of the 1.0% action line — with the
    accelerating flag set (0.93% is more than double the 0.30/0.38/0.52
    mean of the three prior periods).
    """
    august_pct = 0.0093
    previous_three = [0.0030, 0.0038, 0.0052]  # May, Jun, Jul

    result = evaluate_alert(august_pct, previous_three, CONFIG)
    assert result.level == "WATCH"
    assert result.level != "ACTION"
    assert result.accelerating is True


# --- config loader -----------------------------------------------------


def test_load_alert_config_from_toml_matches_real_config():
    config = load_alert_config_from_toml()
    assert config.action_pct == 1.0
    assert config.watch_pct == 0.5
    assert config.accelerating_factor == 2.0
    assert config.accelerating_window == 3
    assert config.investigate_pct == -0.5
