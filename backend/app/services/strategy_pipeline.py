"""Pure synchronous strategy pipeline shared by the live engine and backtests.

Keeping the per-symbol strategy evaluation here guarantees the offline
backtest and the live dashboard run byte-identical signal logic (same
valuation freshness fail-safes, same vetoes, same profit-taking locks),
so backtest results are not optimistic relative to live behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import EnsembleResult, Holding, SymbolConfig
from app.services.market_data import valuation_lag_sessions
from app.services.schedule import TradingCalendarUnavailable
from app.strategies.ensemble import ensemble
from app.strategies.profit_taking import profit_taking_signal
from app.strategies.trend import trend_signal
from app.strategies.valuation import valuation_signal


def apply_growth_bear_policy(
    symbols: list[SymbolConfig],
    policy: str,
    soft_mult: float,
) -> list[SymbolConfig]:
    """Apply the user's growth-bear policy onto profiles with a trend veto.

    ``hard_veto`` forces growth bear regimes back to the config hard veto
    (防守定位); ``soft`` sets ``bear_soft_mult`` so bear regimes keep small
    buys at ``soft_mult`` (追收益定位). Symbols without a trend hard veto
    (core roles) are returned untouched.
    """
    out: list[SymbolConfig] = []
    for s in symbols:
        profile = s.strategy_profile
        if not profile.trend_hard_veto:
            out.append(s)
            continue
        bear = None if policy == "hard_veto" else float(soft_mult)
        out.append(
            s.model_copy(
                update={
                    "strategy_profile": profile.model_copy(
                        update={"bear_soft_mult": bear}
                    )
                }
            )
        )
    return out


@dataclass
class PipelineInputs:
    symbols: list[SymbolConfig]
    latest_by_symbol: dict[str, dict[str, Any]]
    signal_date: str
    base_amount: float
    target_weights: dict[str, float]
    holdings: list[Holding]
    hard_veto_enabled: bool = True
    profit_take_enabled: bool = True
    valuation_reduce_percentile: float = 0.80
    valuation_exit_percentile: float = 0.90
    pool_factor: float = 1.0
    max_mult: float = 2.0


@dataclass
class PipelineOutput:
    items: list[EnsembleResult]
    updated_holdings: list[Holding]
    valuation_issues: list[str] = field(default_factory=list)
    missing_symbols: list[str] = field(default_factory=list)


def run_strategy_pipeline(inputs: PipelineInputs) -> PipelineOutput:
    """Compute ensemble signals for every symbol with a bar.

    Mirrors the previous engine loop exactly: T-1 bar → valuation (with
    freshness fail-safe) + trend (with hysteresis/oversold/bear-soft) +
    profit-taking (valuation-armed trailing stop), merged by weighted average
    with hard vetoes and the full-exit buy lock.
    """
    hmap = {h.symbol: h for h in inputs.holdings}
    items: list[EnsembleResult] = []
    updated_holdings: list[Holding] = []
    valuation_issues: list[str] = []
    missing: list[str] = []

    for sym in inputs.symbols:
        latest = inputs.latest_by_symbol.get(sym.id)
        if not latest:
            missing.append(sym.name)
            continue

        profile = sym.strategy_profile
        tw = float(inputs.target_weights.get(sym.id, sym.target_weight))
        pe_p_raw = latest.get("pe_percentile")
        pb_p_raw = latest.get("pb_percentile")
        pe_raw = latest.get("pe")
        pb_raw = latest.get("pb")
        pe_p = float(pe_p_raw) if pe_p_raw is not None else None
        pb_p = float(pb_p_raw) if pb_p_raw is not None else None
        pe = float(pe_raw) if pe_raw is not None else None
        pb = float(pb_raw) if pb_raw is not None else None
        price = float(latest["close"])
        ma_s = float(latest.get("ma_short") or price)
        ma_l = float(latest.get("ma_long") or price)
        mark_price = (
            float(latest["etf_close"])
            if latest.get("etf_close") is not None
            else price
        )

        holding = hmap.get(sym.id)
        profit_ratio = None
        if holding and holding.shares > 0 and holding.cost_price > 0:
            if holding.market_value is not None:
                holding_price = holding.market_value / holding.shares
            elif latest.get("etf_close") is not None:
                holding_price = float(latest["etf_close"])
            else:
                holding_price = None
            if holding_price is not None:
                profit_ratio = (
                    holding_price - holding.cost_price
                ) / holding.cost_price

        strategy_signals = []
        valuation_p = None
        if sym.valuation_enabled:
            valuation_asof = latest.get("valuation_asof")
            try:
                valuation_lag = valuation_lag_sessions(
                    valuation_asof, inputs.signal_date
                )
            except TradingCalendarUnavailable:
                valuation_lag = None
            s_val = valuation_signal(
                sym.id,
                pe_p,
                pb_p,
                pe=pe,
                pb=pb,
                proxy_label=(
                    sym.valuation_proxy_label if sym.valuation_proxy else None
                ),
                profile=profile,
                valuation_asof=valuation_asof,
                valuation_lag_sessions=valuation_lag,
            )
            strategy_signals.append(s_val)
            if s_val.meta.get("data_missing"):
                valuation_issues.append(sym.name)
            raw_p = s_val.meta.get("p")
            valuation_p = float(raw_p) if raw_p is not None else None

        s_trend = trend_signal(
            sym.id,
            price,
            ma_s,
            ma_l,
            profit_ratio,
            profile=profile,
            prev_state=holding.trend_state if holding else None,
            valuation_p=valuation_p,
        )
        strategy_signals.append(s_trend)

        s_profit = profit_taking_signal(
            sym.id,
            valuation_p=valuation_p,
            price=mark_price,
            has_position=bool(holding and holding.shares > 0),
            current_stage=holding.take_profit_stage if holding else 0,
            trailing_armed=bool(holding.trailing_armed) if holding else False,
            trail_peak_price=holding.trail_peak_price if holding else None,
            valuation_enabled=sym.valuation_enabled,
            enabled=inputs.profit_take_enabled,
            profile=profile.model_copy(
                update={
                    "trail_arm_percentile": inputs.valuation_reduce_percentile,
                    "trail_exit_percentile": inputs.valuation_exit_percentile,
                }
            ),
            pe_percentile=pe_p,
            profit_ratio=profit_ratio,
        )
        strategy_signals.append(s_profit)

        # Full-exit lock: after a valuation-driven full exit (stage 2) the
        # DCA must not keep buying until valuation cools below the re-entry
        # line — otherwise "转入低风险现金管理" would silently re-accumulate.
        exit_p = inputs.valuation_exit_percentile
        block_reason = None
        if (
            inputs.profit_take_enabled
            and holding
            and holding.shares > 0
            and holding.take_profit_stage >= 2
            and valuation_p is not None
            and valuation_p >= exit_p - float(profile.reentry_gap)
        ):
            block_reason = (
                f"清仓止盈生效中（估值 p={valuation_p:.0%} 未回落至再入场线 "
                f"{exit_p - float(profile.reentry_gap):.0%}），暂停新增"
            )

        result = ensemble(
            symbol=sym.id,
            name=sym.name,
            etf_code=sym.etf_code,
            target_weight=tw,
            signals=strategy_signals,
            base_amount=inputs.base_amount,
            hard_veto_enabled=inputs.hard_veto_enabled,
            weights=profile.strategy_weights,
            profile=profile,
            max_mult=inputs.max_mult,
            block_reason=block_reason,
        )
        items.append(result)

        base_h = hmap.get(sym.id) or Holding(symbol=sym.id)
        recommended_stage = int(s_profit.meta.get("recommended_stage") or 0)
        next_stage = (
            max(base_h.take_profit_stage, recommended_stage)
            if base_h.shares > 0
            else 0
        )
        updated_holdings.append(
            Holding(
                symbol=sym.id,
                shares=base_h.shares,
                cost_price=base_h.cost_price,
                market_value=base_h.market_value,
                take_profit_stage=next_stage,
                trend_state=s_trend.meta.get("trend_state"),
                trailing_armed=bool(s_profit.meta.get("trailing_armed")),
                trail_peak_price=s_profit.meta.get("trail_peak_price"),
            )
        )

    return PipelineOutput(
        items=items,
        updated_holdings=updated_holdings,
        valuation_issues=valuation_issues,
        missing_symbols=missing,
    )
