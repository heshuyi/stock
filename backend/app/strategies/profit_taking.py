from __future__ import annotations

from app.models import StrategySignal


def profit_taking_signal(
    symbol: str,
    *,
    pe_percentile: float | None,
    profit_ratio: float | None,
    has_position: bool = False,
    current_stage: int = 0,
    valuation_enabled: bool = True,
    enabled: bool = True,
    profit_threshold: float = 0.30,
    valuation_reduce_threshold: float = 0.80,
    valuation_exit_threshold: float = 0.90,
) -> StrategySignal:
    """Stateless recommendation with an explicit user-confirmed execution stage."""
    reduce_ratio: float | None = None
    recommended_stage = current_stage
    reason = "未触发止盈条件"

    if enabled and has_position and current_stage < 2:
        if (
            valuation_enabled
            and pe_percentile is not None
            and pe_percentile >= valuation_exit_threshold
        ):
            reduce_ratio = 1.0
            recommended_stage = 2
            reason = (
                f"PE 分位 {pe_percentile:.0%} ≥{valuation_exit_threshold:.0%}，"
                "建议清仓并转入低风险现金管理"
            )
        elif current_stage < 1 and (
            (
                valuation_enabled
                and pe_percentile is not None
                and pe_percentile >= valuation_reduce_threshold
            )
            or (profit_ratio is not None and profit_ratio >= profit_threshold)
        ):
            reduce_ratio = 0.5
            recommended_stage = 1
            triggers: list[str] = []
            if (
                valuation_enabled
                and pe_percentile is not None
                and pe_percentile >= valuation_reduce_threshold
            ):
                triggers.append(f"PE 分位 {pe_percentile:.0%}")
            if profit_ratio is not None and profit_ratio >= profit_threshold:
                triggers.append(f"持仓收益 {profit_ratio:.0%}")
            reason = f"{'、'.join(triggers)}触发第一档，建议卖出当前份额的50%"

    action = "reduce" if reduce_ratio else "hold"
    return StrategySignal(
        strategy="profit_taking",
        symbol=symbol,
        action=action,
        multiplier=0.0 if reduce_ratio else 1.0,
        confidence=0.9 if reduce_ratio else 0.7,
        reason=reason,
        reduce_ratio=reduce_ratio,
        meta={
            "pe_percentile": pe_percentile,
            "holding_profit_ratio": profit_ratio,
            "has_position": has_position,
            "current_stage": current_stage,
            "recommended_stage": recommended_stage,
            "profit_threshold": profit_threshold,
            "valuation_reduce_threshold": valuation_reduce_threshold,
            "valuation_exit_threshold": valuation_exit_threshold,
        },
    )
