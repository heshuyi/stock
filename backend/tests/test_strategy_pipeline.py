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
