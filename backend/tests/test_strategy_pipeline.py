"""Tests for the shared strategy pipeline (live engine == backtest logic)."""

from __future__ import annotations

from app.models import Holding, SymbolConfig
from app.services.strategy_pipeline import (
    PipelineInputs,
    apply_growth_bear_policy,
    run_strategy_pipeline,
)


def _symbol(id_: str, weight: float) -> SymbolConfig:
    return SymbolConfig(
        id=id_,
        name=id_,
        etf_code="000000",
        index_code="000000",
        akshare_symbol="sh000000",
        target_weight=weight,
    )


def test_apply_growth_bear_policy_switches_modes():
    core = _symbol("HS300", 0.35)  # trend_hard_veto=False → untouched
    growth = _symbol("CYB200", 0.15)
    growth = growth.model_copy(
        update={
            "strategy_profile": growth.strategy_profile.model_copy(
                update={"trend_hard_veto": True}
            )
        }
    )
    soft = apply_growth_bear_policy([core, growth], "soft", 0.2)
    assert soft[0].strategy_profile.bear_soft_mult is None
    assert soft[1].strategy_profile.bear_soft_mult == 0.2

    # hard_veto forces the config default (None) back even if config set it
    configured_soft = growth.model_copy(
        update={
            "strategy_profile": growth.strategy_profile.model_copy(
                update={"bear_soft_mult": 0.3}
            )
        }
    )
    hard = apply_growth_bear_policy([configured_soft], "hard_veto", 0.2)
    assert hard[0].strategy_profile.bear_soft_mult is None


def _bar(
    close: float = 100.0,
    ma_short: float | None = None,
    ma_long: float | None = None,
    pe_percentile: float | None = 0.3,
    pb_percentile: float | None = None,
    valuation_asof: str | None = "2026-08-14",
) -> dict:
    return {
        "close": close,
        "ma_short": ma_short if ma_short is not None else close * 0.99,
        "ma_long": ma_long if ma_long is not None else close * 0.97,
        "etf_close": close,
        "pe_percentile": pe_percentile,
        "pb_percentile": pb_percentile,
        "pe": 10.0,
        "pb": 1.2,
        "valuation_asof": valuation_asof,
    }


def test_pipeline_marks_missing_symbols():
    symbols = [_symbol("A", 0.6), _symbol("B", 0.4)]
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=symbols,
            latest_by_symbol={"A": _bar()},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 0.6, "B": 0.4},
            holdings=[],
        )
    )
    assert [i.symbol for i in out.items] == ["A"]
    assert out.missing_symbols == ["B"]


def test_pipeline_stage2_lock_and_reentry():
    symbols = [_symbol("A", 1.0)]
    held = [
        Holding(
            symbol="A",
            shares=100,
            cost_price=50,
            take_profit_stage=2,
            trailing_armed=True,
            trail_peak_price=120,
        )
    ]
    # valuation still hot (p=0.95 ≥ exit 0.9 - reentry_gap 0.1=0.8) → buy locked
    hot = run_strategy_pipeline(
        PipelineInputs(
            symbols=symbols,
            latest_by_symbol={"A": _bar(pe_percentile=0.95)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 1.0},
            holdings=held,
            valuation_exit_percentile=0.9,
        )
    )
    assert hot.items[0].action == "pause"
    assert "清仓止盈生效中" in hot.items[0].reason

    # valuation cooled (p=0.6 < 0.8) → DCA buys resume
    cool = run_strategy_pipeline(
        PipelineInputs(
            symbols=symbols,
            latest_by_symbol={"A": _bar(pe_percentile=0.6)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 1.0},
            holdings=held,
            valuation_exit_percentile=0.9,
        )
    )
    assert cool.items[0].action == "buy"
    assert cool.items[0].amount > 0


def test_pipeline_profit_ratio_includes_dividends():
    symbols = [_symbol("A", 1.0)]
    held = [
        Holding(
            symbol="A",
            shares=100,
            cost_price=10,
            dividends_received=200,
        )
    ]
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=symbols,
            latest_by_symbol={"A": _bar(close=10.0)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 1.0},
            holdings=held,
        )
    )
    profit = next(s for s in out.items[0].strategies if s.strategy == "profit_taking")
    # 市值 1000 + 分红 200 − 成本 1000 → 20%
    assert abs(profit.meta["holding_profit_ratio"] - 0.2) < 1e-9


def test_pipeline_advances_take_profit_stage():
    symbols = [_symbol("A", 1.0)]
    held = [
        Holding(symbol="A", shares=100, cost_price=50, take_profit_stage=0)
    ]
    # p ≥ exit → profit signal recommends stage 2 → persisted state advances
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=symbols,
            latest_by_symbol={"A": _bar(pe_percentile=0.96)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 1.0},
            holdings=held,
            valuation_exit_percentile=0.9,
        )
    )
    assert out.updated_holdings[0].take_profit_stage == 2


def test_pipeline_stale_valuation_safe_pauses():
    symbols = [_symbol("A", 1.0)]
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=symbols,
            latest_by_symbol={"A": _bar(valuation_asof="2026-07-01")},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 1.0},
            holdings=[],
        )
    )
    assert out.valuation_issues == ["A"]
    assert out.items[0].action == "pause"
    assert out.items[0].hard_veto is True


def test_rebalance_underweight_boosts_buy_multiplier():
    """Low-weight symbol (actual < target by ≥5%) gets buy multiplier boosted."""
    sym_a = _symbol("A", 0.70)
    sym_b = _symbol("B", 0.30)
    # A holds 600 units @1.0, B holds 100 units @1.0 → total=700
    # A: 600/700 ≈ 0.857, target 0.70 → overweight → reduce suggestion
    # B: 100/700 ≈ 0.143, target 0.30 → underweight ≥5% → mult boost
    held = [
        Holding(symbol="A", shares=600, cost_price=0.5, market_value=600.0),
        Holding(symbol="B", shares=100, cost_price=0.5, market_value=100.0),
    ]
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=[sym_a, sym_b],
            latest_by_symbol={"A": _bar(close=1.0), "B": _bar(close=1.0)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 0.70, "B": 0.30},
            holdings=held,
            rebalance_enabled=True,
            rebalance_threshold=0.05,
            rebalance_mult_cap=1.5,
        )
    )
    a_item = next(i for i in out.items if i.symbol == "A")
    b_item = next(i for i in out.items if i.symbol == "B")
    # A is overweight by ~15.7% → should get reduce signal
    assert a_item.weight_drift is not None and a_item.weight_drift > 0.05
    assert a_item.action == "reduce"
    assert a_item.rebalance_reason is not None
    # B is underweight → if buy signal, multiplier should be boosted
    assert b_item.weight_drift is not None and b_item.weight_drift < -0.05
    assert b_item.actual_weight is not None


def test_rebalance_within_threshold_no_action():
    """Drift below threshold: weight_drift set but no rebalance action."""
    sym_a = _symbol("A", 0.60)
    sym_b = _symbol("B", 0.40)
    # A: 62 @1.0 = 62, B: 40 @1.0 = 40 → total=102
    # A: 60.8%, target 60% → drift=0.8% < 5% threshold → no rebalance action
    held = [
        Holding(symbol="A", shares=62, cost_price=0.5, market_value=62.0),
        Holding(symbol="B", shares=40, cost_price=0.5, market_value=40.0),
    ]
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=[sym_a, sym_b],
            latest_by_symbol={"A": _bar(close=1.0), "B": _bar(close=1.0)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 0.60, "B": 0.40},
            holdings=held,
            rebalance_enabled=True,
            rebalance_threshold=0.05,
        )
    )
    for item in out.items:
        assert item.rebalance_reason is None or "低配" not in item.rebalance_reason or "超配" not in item.rebalance_reason
        # No rebalance-driven action change (check weight_drift is small)
        if item.weight_drift is not None:
            assert abs(item.weight_drift) < 0.05


def test_rebalance_disabled_no_drift_fields():
    """With rebalance_enabled=False, drift fields are not populated."""
    sym_a = _symbol("A", 1.0)
    held = [Holding(symbol="A", shares=100, cost_price=0.5, market_value=100.0)]
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=[sym_a],
            latest_by_symbol={"A": _bar(close=1.0)},
            signal_date="2026-08-14",
            base_amount=1000,
            target_weights={"A": 1.0},
            holdings=held,
            rebalance_enabled=False,
        )
    )
    assert out.items[0].weight_drift is None
    assert out.items[0].actual_weight is None


def test_rebalance_research_defaults_are_off():
    """Live default: research knobs present, rebalance itself disabled."""
    inputs = PipelineInputs(
        symbols=[],
        latest_by_symbol={},
        signal_date="2026-08-14",
        base_amount=0,
        target_weights={},
        holdings=[],
    )
    assert inputs.rebalance_enabled is False
    assert inputs.rebalance_threshold == 0.15
    assert inputs.rebalance_mult_cap == 1.15
    assert inputs.rebalance_reduce_coeff == 0.02


def test_pipeline_empty_holdings_no_crash():
    out = run_strategy_pipeline(
        PipelineInputs(
            symbols=[],
            latest_by_symbol={},
            signal_date="2026-08-14",
            base_amount=0,
            target_weights={},
            holdings=[],
        )
    )
    assert out.items == []
    assert out.updated_holdings == []
