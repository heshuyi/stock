"""DEPRECATED — not used by the v3 ensemble / engine.

Do not wire back into ``compute_dashboard`` without an explicit PRD change
(see PRD §7 非目标). Kept for unit tests and optional experiments.
"""

from __future__ import annotations

from app.models import StrategySignal


def rebalance_signal(
    symbol: str,
    current_weight: float | None,
    target_weight: float,
) -> StrategySignal:
    """Fixed amount + drift vs target weight (legacy)."""
    if current_weight is None:
        return StrategySignal(
            strategy="rebalance",
            symbol=symbol,
            action="buy",
            multiplier=1.1,
            confidence=0.8,
            reason="无持仓，按目标权重启动底仓建设",
            meta={"current_weight": None, "target_weight": target_weight, "drift": None},
        )

    drift = current_weight - target_weight
    reduce_ratio = None
    if drift < -0.08:
        mult, action = 1.4, "buy"
        reason = f"仓位明显偏低 drift={drift:.1%}，优先补仓 ×1.4"
    elif drift < -0.03:
        mult, action = 1.15, "buy"
        reason = f"仓位偏低 drift={drift:.1%}，温和补仓 ×1.15"
    elif abs(drift) <= 0.03:
        mult, action = 1.0, "buy"
        reason = f"仓位接近目标 drift={drift:.1%}，标准定投"
    elif drift > 0.08:
        mult, action = 0.7, "reduce"
        # sell enough to move toward target: approximate fraction of holding
        reduce_ratio = min(drift / max(current_weight, 1e-9), 0.5)
        reason = f"仓位明显偏高 drift={drift:.1%}，减仓回目标"
    else:
        mult, action = 0.7, "buy"
        reason = f"仓位略偏高 drift={drift:.1%}，少投 ×0.7"

    return StrategySignal(
        strategy="rebalance",
        symbol=symbol,
        action=action,
        multiplier=mult,
        confidence=0.85,
        reason=reason,
        reduce_ratio=reduce_ratio,
        meta={
            "current_weight": current_weight,
            "target_weight": target_weight,
            "drift": drift,
        },
    )
