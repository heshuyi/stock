from __future__ import annotations

from app.models import StrategySignal


def grid_signal(symbol: str, drawdown: float) -> StrategySignal:
    """1y high drawdown grid add-on.

Legacy module — v3 ensemble does NOT call this (see PRD §7 非目标).
Kept for unit tests and optional future experiments.
"""
    dd = max(0.0, drawdown)
    if dd < 0.05:
        mult, reason = 0.9, f"回撤 {dd:.1%} <5%，靠近高点少投"
    elif dd < 0.10:
        mult, reason = 1.0, f"回撤 {dd:.1%}，标准定投"
    elif dd < 0.20:
        mult, reason = 1.2, f"回撤 {dd:.1%}，温和网格加码 ×1.2"
    elif dd < 0.30:
        mult, reason = 1.4, f"回撤 {dd:.1%}，网格加码 ×1.4"
    else:
        mult, reason = 1.6, f"回撤 {dd:.1%} ≥30%，深度回撤加码 ×1.6"

    return StrategySignal(
        strategy="grid",
        symbol=symbol,
        action="buy",
        multiplier=mult,
        confidence=0.8,
        reason=reason,
        meta={"drawdown": dd},
    )
