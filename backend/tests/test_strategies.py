"""Unit tests for strategy ensemble boundaries and T-1 clamp."""

from datetime import date

from app.models import StrategySignal
from app.services.engine import _clamp_signal_date, _is_weekly_execution_day
from app.strategies.ensemble import (
    ensemble,
    ensure_minimum_investment,
    normalize_amounts,
)
from app.strategies.grid import grid_signal
from app.strategies.profit_taking import profit_taking_signal
from app.strategies.rebalance import rebalance_signal
from app.strategies.trend import trend_signal
from app.strategies.valuation import valuation_signal


def test_valuation_pause_high():
    s = valuation_signal("HS300", 0.9, 0.85)
    assert s.action == "pause"
    assert s.multiplier == 0


def test_valuation_undervalued():
    s = valuation_signal("HS300", 0.1, 0.15)
    assert s.action == "buy"
    assert s.multiplier == 2.0


def test_valuation_missing_does_not_assume_neutral():
    s = valuation_signal("CYB200", None, None)
    assert s.action == "pause"
    assert s.multiplier == 0
    assert s.meta["data_missing"] is True


def test_valuation_uses_pe_percentile_tiers():
    s = valuation_signal("CYB200", 0.3, None, pe=38)
    assert s.action == "buy"
    assert s.multiplier == 1.5


def test_valuation_marks_market_proxy_in_signal():
    s = valuation_signal(
        "CYB200", 0.3, 0.4, pe=38, proxy_label="创业板市场代理估值"
    )
    assert "创业板市场代理估值" in s.reason
    assert s.meta["proxy_label"] == "创业板市场代理估值"


def test_trend_breakdown():
    s = trend_signal("HS300", price=90, ma_short=100, ma_long=110)
    assert s.action == "pause"
    assert s.multiplier == 0


def test_trend_bull():
    s = trend_signal("HS300", price=120, ma_short=110, ma_long=100)
    assert s.multiplier == 1.0


def test_profit_taking_first_stage_by_return():
    s = profit_taking_signal(
        "HS300", pe_percentile=0.5, profit_ratio=0.31,
        has_position=True, current_stage=0
    )
    assert s.action == "reduce"
    assert s.reduce_ratio == 0.5
    assert s.meta["recommended_stage"] == 1


def test_profit_taking_full_exit_by_valuation():
    s = profit_taking_signal(
        "HS300", pe_percentile=0.91, profit_ratio=0.1,
        has_position=True, current_stage=1
    )
    assert s.action == "reduce"
    assert s.reduce_ratio == 1.0
    assert s.meta["recommended_stage"] == 2


def test_profit_taking_requires_a_position():
    s = profit_taking_signal(
        "HS300", pe_percentile=0.95, profit_ratio=None, has_position=False
    )
    assert s.action == "hold"
    assert s.reduce_ratio is None


def test_profit_taking_can_ignore_disabled_valuation():
    s = profit_taking_signal(
        "X",
        pe_percentile=0.95,
        profit_ratio=0.1,
        has_position=True,
        current_stage=0,
        valuation_enabled=False,
    )
    assert s.action == "hold"


def test_rebalance_underweight():
    s = rebalance_signal("HS300", current_weight=0.2, target_weight=0.4)
    assert s.multiplier == 1.4


def test_grid_deep_drawdown():
    s = grid_signal("HS300", 0.35)
    assert s.multiplier == 1.6


def test_hard_veto_blocks_grid():
    signals = [
        valuation_signal("HS300", 0.9, 0.9),
        trend_signal("HS300", 90, 100, 110),
        rebalance_signal("HS300", None, 0.4),
        grid_signal("HS300", 0.35),
    ]
    result = ensemble(
        symbol="HS300",
        name="沪深300",
        etf_code="510300",
        target_weight=0.4,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
    )
    assert result.hard_veto is True
    assert result.multiplier == 0
    assert result.action == "pause"
    assert result.amount == 0


def test_missing_valuation_blocks_new_buy():
    signals = [
        valuation_signal("CYB200", None, None),
        trend_signal("CYB200", 120, 110, 100),
        rebalance_signal("CYB200", None, 0.15),
        grid_signal("CYB200", 0.25),
    ]
    result = ensemble(
        symbol="CYB200",
        name="创业板200",
        etf_code="159572",
        target_weight=0.15,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
    )
    assert result.hard_veto is True
    assert result.action == "pause"
    assert result.amount == 0


def test_valuation_trend_weighted_average():
    signals = [
        StrategySignal(strategy="valuation", symbol="X", action="buy", multiplier=1.0, reason="a"),
        StrategySignal(strategy="trend", symbol="X", action="buy", multiplier=0.5, reason="b"),
    ]
    result = ensemble(
        symbol="X",
        name="X",
        etf_code="000",
        target_weight=0.4,
        signals=signals,
        base_amount=10000,
    )
    assert abs(result.multiplier - 0.8) < 1e-6
    assert abs(result.amount - 3200) < 1e-6


def test_normalize_cap():
    from app.models import EnsembleResult

    items = [
        EnsembleResult(
            symbol="A",
            name="A",
            etf_code="1",
            target_weight=0.5,
            action="buy",
            multiplier=2,
            amount=10000,
            reason="r",
            strategies=[],
        ),
        EnsembleResult(
            symbol="B",
            name="B",
            etf_code="2",
            target_weight=0.5,
            action="buy",
            multiplier=2,
            amount=10000,
            reason="r",
            strategies=[],
        ),
    ]
    scaled, normalized = normalize_amounts(items, base_amount=10000, cap_ratio=1.5)
    assert normalized is True
    assert abs(sum(i.amount for i in scaled) - 15000) < 1e-6


def test_clamp_signal_date_rejects_future(monkeypatch):
    monkeypatch.setattr(
        "app.services.engine.market_store.resolve_signal_date",
        lambda today=None: "2026-07-29",
    )
    d, mode = _clamp_signal_date("2026-07-30")
    assert d == "2026-07-29"
    assert mode == "T-1"

    d2, mode2 = _clamp_signal_date("2026-07-20")
    assert d2 == "2026-07-20"
    assert mode2 == "historical"

    d3, mode3 = _clamp_signal_date(None)
    assert d3 == "2026-07-29"
    assert mode3 == "T-1"


def test_weekly_execution_uses_first_trading_day():
    assert _is_weekly_execution_day(
        "2026-07-24", execution_day=date(2026, 7, 27)
    )
    assert not _is_weekly_execution_day(
        "2026-07-27", execution_day=date(2026, 7, 28)
    )


def test_minimum_investment_floor():
    from app.models import EnsembleResult

    items = [
        EnsembleResult(
            symbol="HS300",
            name="沪深300",
            etf_code="510300",
            target_weight=0.35,
            action="pause",
            multiplier=0,
            amount=0,
            reason="r",
            strategies=[],
            hard_veto=True,
        ),
        EnsembleResult(
            symbol="ZZ500",
            name="中证500",
            etf_code="510500",
            target_weight=0.35,
            action="pause",
            multiplier=0,
            amount=0,
            reason="r",
            strategies=[],
            hard_veto=True,
        ),
        EnsembleResult(
            symbol="CYB200",
            name="创业板200",
            etf_code="159572",
            target_weight=0.15,
            action="pause",
            multiplier=0,
            amount=0,
            reason="r",
            strategies=[],
            hard_veto=True,
        ),
    ]
    adjusted, applied = ensure_minimum_investment(items, 10000, floor_ratio=0.25)
    assert applied is True
    assert sum(i.amount for i in adjusted) == 2500
    assert adjusted[0].action == "buy"
    assert adjusted[1].action == "buy"
    assert adjusted[2].amount == 0


def test_minimum_investment_can_be_disabled():
    from app.models import EnsembleResult

    items = [
        EnsembleResult(
            symbol="HS300",
            name="沪深300",
            etf_code="510300",
            target_weight=0.35,
            action="pause",
            multiplier=0,
            amount=0,
            reason="估值与趋势共同触发暂停",
            strategies=[],
            hard_veto=True,
        )
    ]
    adjusted, applied = ensure_minimum_investment(items, 10000, floor_ratio=0)
    assert applied is False
    assert adjusted[0].action == "pause"
    assert adjusted[0].amount == 0
    assert adjusted[0].hard_veto is True
