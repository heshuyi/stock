from __future__ import annotations

from app.models import StrategySignal


def trend_signal(
    symbol: str,
    price: float,
    ma_short: float,
    ma_long: float,
    holding_profit_ratio: float | None = None,
) -> StrategySignal:
    """MA trend filter for weekly A-share broad-index accumulation."""
    trend_break = False
    if price > ma_short and ma_short > ma_long:
        mult, action, reason = 1.0, "buy", "多头排列（价>MA60>MA120），趋势允许定投"
    elif price > ma_long:
        mult, action, reason = 0.75, "buy", "价格仍在MA120上方，弱趋势折价定投"
    else:
        trend_break = True
        mult, action = 0.0, "pause"
        reason = "价格跌破MA120，趋势破位，暂停新增资金"

    return StrategySignal(
        strategy="trend",
        symbol=symbol,
        action=action,
        multiplier=mult,
        confidence=0.85,
        reason=reason,
        reduce_ratio=None,
        meta={
            "price": price,
            "ma_short": ma_short,
            "ma_long": ma_long,
            "holding_profit_ratio": holding_profit_ratio,
            "trend_break": trend_break,
        },
    )
