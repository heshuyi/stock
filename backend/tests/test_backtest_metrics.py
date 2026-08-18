"""Smoke tests for offline backtest metrics."""

from __future__ import annotations

from datetime import date

from scripts.backtest_strategy import (
    _resolve_growth_policy,
    calculate_metrics,
    max_drawdown,
    segment_metrics,
    xirr,
)


def test_xirr_and_drawdown_helpers():
    result = xirr(
        [
            (date(2024, 1, 1), -1000),
            (date(2025, 1, 1), 1100),
        ]
    )
    assert result is not None
    assert abs(result - 0.10) < 0.002
    assert max_drawdown([100, 120, 90, 110]) == -0.25


def test_xirr_negative_return():
    result = xirr(
        [
            (date(2024, 1, 1), -1000),
            (date(2025, 1, 1), 100),
        ]
    )
    # 2024 is a leap year: 366 days → (1+r)^(366/365) = 0.1
    exact = 0.1 ** (1 / (366 / 365)) - 1
    assert result is not None
    assert abs(result - exact) < 1e-9


def test_metric_smoke():
    metrics = calculate_metrics(
        [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        [100, 111, 108],
        [100, 10, 0],
        110,
    )
    assert metrics["total_invested"] == 110
    assert metrics["ending_value"] == 108
    assert metrics["max_drawdown"] < 0


def test_segment_metrics_carries_capital_into_xirr():
    dates = [date(2024, 1, 1), date(2024, 6, 30), date(2025, 6, 30)]
    values = [1000, 1050, 1210]
    contributions = [0, 0, 0]
    seg = segment_metrics(dates, values, contributions, lo=1)
    assert seg["starting_value"] == 1000
    assert seg["ending_value"] == 1210
    assert abs(seg["xirr"] - 0.21) < 1e-3
    assert abs(seg["twr"] - 0.21) < 1e-3


def test_resolve_growth_policy_aliases():
    class _Args:
        growth_bear_policy = "hard_veto"
        variant = "baseline"

    assert _resolve_growth_policy(_Args()) == "hard_veto"
    a = _Args()
    a.growth_bear_policy = "soft"
    assert _resolve_growth_policy(a) == "soft"
    a2 = _Args()
    a2.variant = "growth-bear-soft"
    assert _resolve_growth_policy(a2) == "soft"
