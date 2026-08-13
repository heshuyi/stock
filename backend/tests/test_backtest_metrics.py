"""Smoke tests for offline backtest metrics."""

from datetime import date

from scripts.backtest_strategy import calculate_metrics, max_drawdown, xirr


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
