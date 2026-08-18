"""Unit tests for differentiated strategy profiles and ensemble boundaries."""

from app.models import StrategyProfile, StrategySignal, TrendMults
from app.services.engine import _clamp_signal_date
from app.strategies.ensemble import (
    apply_cash_pool,
    cash_pool_factor,
    ensemble,
    ensure_minimum_investment,
    normalize_amounts,
)
from app.strategies.profit_taking import profit_taking_signal
from app.strategies.trend import trend_signal
from app.strategies.valuation import valuation_signal


CORE = StrategyProfile(
    valuation_mode="pe",
    pause_percentile=0.90,
    tier_mults=[1.8, 1.4, 1.0, 0.5],
    trend_hard_veto=False,
    trend_hysteresis=True,
    trend_mults=TrendMults(bull=1.0, mild_bull=0.85, sandwich=0.55, bear=0.35),
    strategy_weights={"valuation": 0.7, "trend": 0.3},
    trail_arm_percentile=0.90,
    trail_drawdown=0.10,
    trail_exit_percentile=0.95,
)

GROWTH = StrategyProfile(
    valuation_mode="pe_pb_composite",
    pe_weight=0.55,
    pb_weight=0.45,
    pause_percentile=0.80,
    tier_mults=[1.6, 1.3, 1.0, 0.4],
    trend_hard_veto=True,
    trend_hysteresis=False,
    trend_mults=TrendMults(bull=1.0, mild_bull=0.7, sandwich=0.35, bear=0.0),
    oversold_unlock=True,
    oversold_p=0.15,
    oversold_bias=-0.12,
    oversold_mult=0.25,
    strategy_weights={"valuation": 0.55, "trend": 0.45},
    trail_arm_percentile=0.80,
    trail_drawdown=0.08,
    trail_exit_percentile=0.90,
)


def test_valuation_pause_high():
    s = valuation_signal("HS300", 0.95, 0.85, profile=CORE)
    assert s.action == "pause"
    assert s.multiplier == 0


def test_valuation_core_allows_high_but_below_pause():
    s = valuation_signal("HS300", 0.85, 0.85, profile=CORE)
    assert s.action == "buy"
    assert s.multiplier == 0.5


def test_valuation_undervalued():
    s = valuation_signal("HS300", 0.1, 0.15, profile=CORE)
    assert s.action == "buy"
    assert s.multiplier == 1.8


def test_valuation_missing_does_not_assume_neutral():
    s = valuation_signal("CYB200", None, None, profile=GROWTH)
    assert s.action == "pause"
    assert s.multiplier == 0
    assert s.meta["data_missing"] is True


def test_valuation_stale_beyond_five_sessions_safe_pauses():
    s = valuation_signal(
        "HS300",
        0.2,
        0.3,
        profile=CORE,
        valuation_asof="2026-07-27",
        valuation_lag_sessions=6,
    )
    assert s.action == "pause"
    assert s.multiplier == 0
    assert s.meta["data_missing"] is True
    assert "估值滞后 6 个交易日" in s.reason


def test_valuation_five_session_lag_is_still_usable():
    s = valuation_signal(
        "HS300",
        0.2,
        0.3,
        profile=CORE,
        valuation_asof="2026-07-28",
        valuation_lag_sessions=5,
    )
    assert s.action == "buy"
    assert s.meta["data_missing"] is False
    assert s.meta["valuation_lag_sessions"] == 5


def test_valuation_composite_uses_pe_pb_weights():
    s = valuation_signal("CYB200", 0.2, 0.8, pe=30, pb=4, profile=GROWTH)
    expected = 0.55 * 0.2 + 0.45 * 0.8
    assert abs(s.meta["p"] - expected) < 1e-9
    assert s.action == "buy"


def test_valuation_marks_market_proxy_in_signal():
    s = valuation_signal(
        "CYB200",
        0.3,
        0.4,
        pe=38,
        proxy_label="创业板市场代理估值",
        profile=GROWTH,
    )
    assert "创业板市场代理估值" in s.reason
    assert s.meta["proxy_label"] == "创业板市场代理估值"


def test_trend_bear_alignment():
    s = trend_signal("CYB200", price=90, ma_short=95, ma_long=110, profile=GROWTH)
    assert s.meta["trend_state"] == "bear"
    assert s.action == "pause"
    assert s.meta["trend_break"] is True


def test_trend_sandwich_not_hard_break():
    s = trend_signal(
        "CYB200", price=90, ma_short=120, ma_long=110, profile=GROWTH
    )
    assert s.meta["trend_state"] == "sandwich"
    assert s.meta["trend_break"] is False
    assert s.multiplier == 0.35


def test_trend_core_bear_still_buys():
    s = trend_signal("HS300", price=90, ma_short=95, ma_long=110, profile=CORE)
    assert s.meta["trend_state"] == "bear"
    assert s.action == "buy"
    assert s.multiplier == 0.35
    assert s.meta["trend_break"] is False


def test_trend_hysteresis_holds_mild_bull_near_ma():
    # Bias just below 0 but within -1% band → stay mild_bull
    s = trend_signal(
        "HS300",
        price=99.5,
        ma_short=100,
        ma_long=100,
        profile=CORE,
        prev_state="mild_bull",
    )
    assert s.meta["raw_state"] == "sandwich"
    assert s.meta["trend_state"] == "mild_bull"


def test_trend_bull():
    s = trend_signal("HS300", price=120, ma_short=110, ma_long=100, profile=CORE)
    assert s.multiplier == 1.0


def test_oversold_unlock():
    s = trend_signal(
        "CYB200",
        price=80,
        ma_short=90,
        ma_long=100,
        profile=GROWTH,
        valuation_p=0.10,
    )
    assert s.meta["oversold_unlock"] is True
    assert s.action == "buy"
    assert s.multiplier == 0.25
    assert s.meta["trend_break"] is False


def test_profit_taking_trailing_drawdown():
    s = profit_taking_signal(
        "HS300",
        valuation_p=0.92,
        price=90,
        has_position=True,
        current_stage=0,
        trailing_armed=True,
        trail_peak_price=100,
        profile=CORE,
    )
    assert s.action == "reduce"
    assert s.reduce_ratio == 0.5
    assert s.meta["recommended_stage"] == 1


def test_profit_taking_exit_by_valuation():
    s = profit_taking_signal(
        "HS300",
        valuation_p=0.96,
        price=100,
        has_position=True,
        current_stage=1,
        profile=CORE,
    )
    assert s.action == "reduce"
    assert s.reduce_ratio == 1.0
    assert s.meta["recommended_stage"] == 2


def test_profit_taking_global_settings_override_profile():
    """Engine overlays user settings onto symbol profile for arm/exit lines."""
    profile = CORE.model_copy(
        update={
            "trail_arm_percentile": 0.90,
            "trail_exit_percentile": 0.95,
        }
    )
    from_settings = profile.model_copy(
        update={
            "trail_arm_percentile": 0.75,
            "trail_exit_percentile": 0.85,
        }
    )
    s = profit_taking_signal(
        "HS300",
        valuation_p=0.86,
        price=100,
        has_position=True,
        current_stage=0,
        profile=from_settings,
    )
    assert s.action == "reduce"
    assert s.reduce_ratio == 1.0


def test_profit_taking_ignores_return_trigger():
    s = profit_taking_signal(
        "HS300",
        valuation_p=0.5,
        price=100,
        has_position=True,
        current_stage=0,
        profit_ratio=0.5,
        profile=CORE,
    )
    assert s.action == "hold"
    assert s.reduce_ratio is None


def test_profit_taking_requires_a_position():
    s = profit_taking_signal(
        "HS300",
        valuation_p=0.95,
        price=100,
        has_position=False,
        current_stage=2,
        trailing_armed=True,
        trail_peak_price=120,
        profile=CORE,
    )
    assert s.action == "hold"
    assert s.reduce_ratio is None
    assert s.meta["recommended_stage"] == 0
    assert s.meta["trailing_armed"] is False
    assert s.meta["trail_peak_price"] is None


def test_hard_veto_growth_bear_blocks_buy():
    signals = [
        valuation_signal("CYB200", 0.3, 0.4, profile=GROWTH),
        trend_signal("CYB200", 90, 95, 110, profile=GROWTH, valuation_p=0.3),
    ]
    result = ensemble(
        symbol="CYB200",
        name="创业板200",
        etf_code="159572",
        target_weight=0.15,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
        profile=GROWTH,
        weights=GROWTH.strategy_weights,
    )
    assert result.hard_veto is True
    assert result.amount == 0
    assert result.action == "pause"


def test_core_bear_not_hard_veto():
    signals = [
        valuation_signal("HS300", 0.5, 0.5, profile=CORE),
        trend_signal("HS300", 90, 95, 110, profile=CORE),
    ]
    result = ensemble(
        symbol="HS300",
        name="沪深300",
        etf_code="510300",
        target_weight=0.35,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
        profile=CORE,
        weights=CORE.strategy_weights,
    )
    assert result.hard_veto is False
    assert result.action == "buy"
    assert result.amount > 0


def test_ensemble_uses_weighted_average_not_product():
    """低估加码 + 夹层降频：加权平均应明显高于相乘，避免核心仓加码被抹平。"""
    signals = [
        StrategySignal(
            strategy="valuation",
            symbol="HS300",
            action="buy",
            multiplier=1.8,
            confidence=0.9,
            reason="低估",
        ),
        StrategySignal(
            strategy="trend",
            symbol="HS300",
            action="buy",
            multiplier=0.55,
            confidence=0.85,
            reason="夹层",
        ),
        StrategySignal(
            strategy="profit_taking",
            symbol="HS300",
            action="hold",
            multiplier=1.0,
            confidence=0.7,
            reason="未触发",
        ),
    ]
    product = 1.8 * 0.55  # 0.99
    result = ensemble(
        symbol="HS300",
        name="沪深300",
        etf_code="510300",
        target_weight=0.35,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
        profile=CORE,
        weights=CORE.strategy_weights,
    )
    # weighted average is rescaled to the reachable cap: avg 1.425 → ×(2.0/1.56)
    expected = (0.7 * 1.8 + 0.3 * 0.55) * (2.0 / (0.7 * 1.8 + 0.3 * 1.0))
    assert abs(result.multiplier - expected) < 1e-3  # rounded to 4dp
    assert result.multiplier > product
    assert "加权平均" in result.reason
    assert "非相乘" in result.reason
    assert "缩放" in result.reason


def test_ensemble_scale_to_cap_can_be_disabled():
    """scale_to_cap=False 保留纯加权平均语义（上限由 max_mult 直接钳制）。"""
    signals = [
        StrategySignal(
            strategy="valuation",
            symbol="HS300",
            action="buy",
            multiplier=1.8,
            confidence=0.9,
            reason="低估",
        ),
        StrategySignal(
            strategy="trend",
            symbol="HS300",
            action="buy",
            multiplier=0.55,
            confidence=0.85,
            reason="夹层",
        ),
    ]
    profile = CORE.model_copy(update={"scale_to_cap": False})
    result = ensemble(
        symbol="HS300",
        name="沪深300",
        etf_code="510300",
        target_weight=0.35,
        signals=signals,
        base_amount=10000,
        profile=profile,
        weights=profile.strategy_weights,
    )
    expected = 0.7 * 1.8 + 0.3 * 0.55  # 1.425
    assert abs(result.multiplier - expected) < 1e-9
    assert "缩放" not in result.reason


def test_oversold_unlock_bypasses_hard_veto():
    signals = [
        valuation_signal("CYB200", 0.1, 0.1, profile=GROWTH),
        trend_signal(
            "CYB200", 80, 90, 100, profile=GROWTH, valuation_p=0.1
        ),
    ]
    result = ensemble(
        symbol="CYB200",
        name="创业板200",
        etf_code="159572",
        target_weight=0.15,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
        profile=GROWTH,
        weights=GROWTH.strategy_weights,
    )
    assert result.hard_veto is False
    assert result.action == "buy"
    assert abs(result.multiplier - 0.25) < 1e-9


def test_missing_valuation_blocks_new_buy():
    signals = [
        valuation_signal("CYB200", None, None, profile=GROWTH),
        trend_signal("CYB200", 120, 110, 100, profile=GROWTH),
    ]
    result = ensemble(
        symbol="CYB200",
        name="创业板200",
        etf_code="159572",
        target_weight=0.15,
        signals=signals,
        base_amount=10000,
        hard_veto_enabled=True,
        profile=GROWTH,
    )
    assert result.hard_veto is True
    assert result.action == "pause"
    assert result.amount == 0


def test_valuation_trend_weighted_average():
    signals = [
        StrategySignal(
            strategy="valuation", symbol="X", action="buy", multiplier=1.0, reason="a"
        ),
        StrategySignal(
            strategy="trend", symbol="X", action="buy", multiplier=0.5, reason="b"
        ),
    ]
    result = ensemble(
        symbol="X",
        name="X",
        etf_code="000",
        target_weight=0.4,
        signals=signals,
        base_amount=10000,
        weights={"valuation": 0.6, "trend": 0.4},
    )
    # default profile: reachable ceiling 0.6*2.0+0.4*1.0=1.6 → scale 2.0/1.6
    assert abs(result.multiplier - 1.0) < 1e-6
    assert abs(result.amount - 4000) < 1e-6
    # without cap scaling the raw weighted average is preserved
    raw = ensemble(
        symbol="X",
        name="X",
        etf_code="000",
        target_weight=0.4,
        signals=signals,
        base_amount=10000,
        weights={"valuation": 0.6, "trend": 0.4},
        profile=StrategyProfile(scale_to_cap=False),
    )
    assert abs(raw.multiplier - 0.8) < 1e-6


def test_max_mult_reachable_at_full_max():
    """At full undervaluation + bull trend the rescaled average hits max_mult."""
    signals = [
        StrategySignal(
            strategy="valuation", symbol="X", action="buy", multiplier=1.8, reason="a"
        ),
        StrategySignal(
            strategy="trend", symbol="X", action="buy", multiplier=1.0, reason="b"
        ),
    ]
    result = ensemble(
        symbol="X",
        name="X",
        etf_code="000",
        target_weight=0.4,
        signals=signals,
        base_amount=10000,
        profile=CORE,
        weights=CORE.strategy_weights,
        max_mult=2.0,
    )
    assert abs(result.multiplier - 2.0) < 1e-9


def test_valuation_uses_configurable_bands():
    profile = CORE.model_copy(
        update={"band_low": 0.1, "band_mid": 0.3, "band_high": 0.5}
    )
    assert valuation_signal("HS300", 0.05, 0.05, profile=profile).multiplier == 1.8
    assert valuation_signal("HS300", 0.2, 0.2, profile=profile).multiplier == 1.4
    assert valuation_signal("HS300", 0.4, 0.4, profile=profile).multiplier == 1.0
    assert valuation_signal("HS300", 0.7, 0.7, profile=profile).multiplier == 0.5
    # band_high must stay below pause_percentile
    from app.models import StrategyProfile as _SP

    try:
        _SP.model_validate(
            CORE.model_copy(update={"band_high": 0.95}).model_dump()
        )
        raise AssertionError("expected validation error")
    except Exception:
        pass


def test_trend_bear_soft_mult_softens_hard_veto():
    soft = GROWTH.model_copy(update={"bear_soft_mult": 0.2})
    s = trend_signal("CYB200", price=90, ma_short=95, ma_long=110, profile=soft)
    assert s.meta["trend_state"] == "bear"
    assert s.meta["trend_break"] is False
    assert s.meta["bear_soft"] is True
    assert s.action == "buy"
    assert abs(s.multiplier - 0.2) < 1e-9

    result = ensemble(
        symbol="CYB200",
        name="创业板200",
        etf_code="159572",
        target_weight=0.15,
        signals=[valuation_signal("CYB200", 0.3, 0.4, profile=soft), s],
        base_amount=10000,
        hard_veto_enabled=True,
        profile=soft,
        weights=soft.strategy_weights,
    )
    assert result.hard_veto is False
    assert result.action == "buy"
    assert result.amount > 0


def test_ensemble_block_reason_locks_buy_but_allows_reduce():
    buy_signals = [
        StrategySignal(
            strategy="valuation", symbol="X", action="buy", multiplier=1.0, reason="a"
        ),
        StrategySignal(
            strategy="trend", symbol="X", action="buy", multiplier=1.0, reason="b"
        ),
    ]
    locked = ensemble(
        symbol="X",
        name="X",
        etf_code="000",
        target_weight=0.4,
        signals=buy_signals,
        base_amount=10000,
        block_reason="清仓止盈生效中，暂停新增",
    )
    assert locked.hard_veto is True
    assert locked.action == "pause"
    assert locked.amount == 0
    assert "清仓止盈生效中" in locked.reason

    reduce_signals = buy_signals + [
        StrategySignal(
            strategy="profit_taking",
            symbol="X",
            action="reduce",
            multiplier=0.0,
            reduce_ratio=1.0,
            reason="清仓级减持",
        )
    ]
    locked_reduce = ensemble(
        symbol="X",
        name="X",
        etf_code="000",
        target_weight=0.4,
        signals=reduce_signals,
        base_amount=10000,
        block_reason="清仓止盈生效中，暂停新增",
    )
    assert locked_reduce.action == "reduce"
    assert locked_reduce.reduce_ratio == 1.0


def test_cash_pool_factor_and_scale():
    assert cash_pool_factor(0, 10000, 36) == 1.0
    # empty dry powder → factor 0 (real ammunition gauge), not a 0.35 floor
    assert cash_pool_factor(0, 10000, 36, enabled=True) == 0.0
    assert (
        abs(cash_pool_factor(360000, 10000, 36, enabled=True) - 1.0)
        < 1e-9
    )
    thin = cash_pool_factor(50000, 10000, 36, enabled=True)
    assert 0 < thin < 0.5
    from app.models import EnsembleResult

    items = [
        EnsembleResult(
            symbol="A",
            name="A",
            etf_code="1",
            target_weight=0.5,
            action="buy",
            multiplier=1,
            amount=1000,
            reason="r",
            strategies=[],
        )
    ]
    scaled, applied = apply_cash_pool(items, 0.4)
    assert applied is True
    assert scaled[0].amount == 400.0
    assert scaled[0].multiplier == 0.4


def test_cash_pool_zero_halts_buys_not_reductions():
    from app.models import EnsembleResult

    items = [
        EnsembleResult(
            symbol="A",
            name="A",
            etf_code="1",
            target_weight=0.5,
            action="buy",
            multiplier=1,
            amount=1000,
            reason="r",
            strategies=[],
        ),
        EnsembleResult(
            symbol="B",
            name="B",
            etf_code="2",
            target_weight=0.5,
            action="reduce",
            multiplier=0,
            amount=0,
            reduce_ratio=0.5,
            reason="止盈",
            strategies=[],
        ),
    ]
    adjusted, applied = apply_cash_pool(items, 0.0)
    assert applied is True
    assert adjusted[0].action == "hold"
    assert adjusted[0].amount == 0
    assert "弹药为空" in adjusted[0].reason
    assert adjusted[1].action == "reduce"
    assert adjusted[1].reduce_ratio == 0.5


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
            target_weight=0.25,
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
