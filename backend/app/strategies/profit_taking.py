from __future__ import annotations

from app.models import StrategyProfile, StrategySignal


def profit_taking_signal(
    symbol: str,
    *,
    valuation_p: float | None,
    price: float | None,
    has_position: bool = False,
    current_stage: int = 0,
    trailing_armed: bool = False,
    trail_peak_price: float | None = None,
    valuation_enabled: bool = True,
    enabled: bool = True,
    profile: StrategyProfile | None = None,
    # legacy kwargs kept for older call sites / tests
    pe_percentile: float | None = None,
    profit_ratio: float | None = None,
    profit_threshold: float = 0.30,
    valuation_reduce_threshold: float = 0.80,
    valuation_exit_threshold: float = 0.90,
) -> StrategySignal:
    """Valuation-armed trailing stop; no return-based hard trigger."""
    profile = profile or StrategyProfile(
        trail_arm_percentile=valuation_reduce_threshold,
        trail_exit_percentile=valuation_exit_threshold,
    )
    p = valuation_p if valuation_p is not None else pe_percentile
    reduce_ratio: float | None = None
    recommended_stage = current_stage
    next_armed = trailing_armed
    next_peak = trail_peak_price
    reason = "未触发止盈条件"

    if enabled and has_position and current_stage < 2 and valuation_enabled:
        arm = float(profile.trail_arm_percentile)
        exit_p = float(profile.trail_exit_percentile)
        dd = float(profile.trail_drawdown)
        disarm_gap = float(profile.trail_disarm_gap)

        if p is not None and p >= exit_p:
            reduce_ratio = 1.0
            recommended_stage = 2
            reason = (
                f"估值 p={p:.0%} ≥{exit_p:.0%}，建议清仓级减持并转入低风险现金管理"
            )
            next_armed = True
            if price is not None:
                next_peak = (
                    price
                    if next_peak is None
                    else max(next_peak, price)
                )
        elif p is not None and p >= arm:
            next_armed = True
            if price is not None:
                next_peak = (
                    price
                    if next_peak is None
                    else max(float(next_peak), price)
                )
            if (
                current_stage < 1
                and next_peak
                and price is not None
                and next_peak > 0
            ):
                drawdown = (next_peak - price) / next_peak
                if drawdown >= dd:
                    reduce_ratio = 0.5
                    recommended_stage = 1
                    reason = (
                        f"估值武装（p≥{arm:.0%}）后自高点回撤 "
                        f"{drawdown:.1%}≥{dd:.0%}，建议卖出当前份额的50%"
                    )
                else:
                    reason = (
                        f"估值已武装（p={p:.0%}≥{arm:.0%}），"
                        f"追踪峰值回撤 {drawdown:.1%}（触发线 {dd:.0%}）"
                    )
            else:
                reason = f"估值已武装（p={p:.0%}≥{arm:.0%}），等待回撤触发或继续持有"
        elif (
            trailing_armed
            and p is not None
            and p < arm - disarm_gap
        ):
            next_armed = False
            next_peak = None
            reason = (
                f"估值回落至 p={p:.0%}（低于武装线 {arm:.0%} 超过 "
                f"{disarm_gap:.0%}），解除追踪止盈"
            )
        elif trailing_armed and price is not None:
            next_armed = True
            if next_peak is None:
                next_peak = price
            else:
                next_peak = max(float(next_peak), price)
            if current_stage < 1 and next_peak > 0:
                drawdown = (next_peak - price) / next_peak
                if drawdown >= dd:
                    reduce_ratio = 0.5
                    recommended_stage = 1
                    reason = (
                        f"追踪止盈自高点回撤 {drawdown:.1%}≥{dd:.0%}，"
                        "建议卖出当前份额的50%"
                    )

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
            "valuation_p": p,
            "pe_percentile": pe_percentile,
            "holding_profit_ratio": profit_ratio,
            "price": price,
            "has_position": has_position,
            "current_stage": current_stage,
            "recommended_stage": recommended_stage,
            "trailing_armed": next_armed,
            "trail_peak_price": next_peak,
            "trail_arm_percentile": profile.trail_arm_percentile,
            "trail_drawdown": profile.trail_drawdown,
            "trail_exit_percentile": profile.trail_exit_percentile,
            "profit_threshold_ignored": profit_threshold,
        },
    )
