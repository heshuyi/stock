from __future__ import annotations

from app.models import StrategyProfile, StrategySignal, TrendState


def _raw_regime(price: float, ma_short: float, ma_long: float) -> TrendState:
    if price > ma_short and ma_short > ma_long:
        return "bull"
    if price > ma_long:
        return "mild_bull"
    if ma_short >= ma_long:
        return "sandwich"
    return "bear"


def _apply_hysteresis(
    raw: TrendState,
    prev: TrendState | None,
    bias: float,
) -> TrendState:
    """±1% Bias band to reduce MA120 flip-flops for core holdings."""
    if prev is None:
        return raw

    up_side = {"bull", "mild_bull"}
    down_side = {"sandwich", "bear"}

    if prev in up_side and raw in down_side:
        return prev if bias >= -0.01 else raw
    if prev in down_side and raw in up_side:
        return prev if bias <= 0.01 else raw
    return raw


def trend_signal(
    symbol: str,
    price: float,
    ma_short: float,
    ma_long: float,
    holding_profit_ratio: float | None = None,
    profile: StrategyProfile | None = None,
    prev_state: TrendState | None = None,
    valuation_p: float | None = None,
) -> StrategySignal:
    """Dual-MA regime filter with optional hysteresis and oversold unlock meta."""
    profile = profile or StrategyProfile()
    bias = (price - ma_long) / ma_long if ma_long else 0.0
    raw = _raw_regime(price, ma_short, ma_long)
    state = (
        _apply_hysteresis(raw, prev_state, bias)
        if profile.trend_hysteresis
        else raw
    )

    mults = profile.trend_mults
    mult = float(getattr(mults, state))
    labels = {
        "bull": "多头排列（价>MA60>MA120）",
        "mild_bull": "偏多（价>MA120）",
        "sandwich": "夹层（价<MA120 但未空头排列）",
        "bear": "空头排列（价与MA60均在MA120下）",
    }
    trend_break = state == "bear" and profile.trend_hard_veto and mult <= 0
    oversold_unlock = False
    if (
        state == "bear"
        and profile.oversold_unlock
        and valuation_p is not None
        and valuation_p < profile.oversold_p
        and bias < profile.oversold_bias
    ):
        oversold_unlock = True
        mult = float(profile.oversold_mult)
        trend_break = False
        action = "buy"
        reason = (
            f"{labels[state]}；超跌解封（估值p={valuation_p:.0%}，"
            f"Bias={bias:.1%}）按 {mult:.2f}× 小额吸纳"
        )
    elif (
        state == "bear"
        and profile.trend_hard_veto
        and profile.bear_soft_mult is not None
    ):
        # Optional soft-growth mode: keep small buys instead of a hard veto.
        mult = float(profile.bear_soft_mult)
        trend_break = False
        action = "buy"
        reason = (
            f"{labels[state]}；成长仓软降频按 {mult:.2f}× 小额续投（非硬停）"
        )
    elif mult <= 1e-9:
        action = "pause"
        reason = f"{labels[state]}，暂停新增资金"
    else:
        action = "buy"
        reason = f"{labels[state]}，趋势倍数 {mult:.2f}×"

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
            "bias": bias,
            "raw_state": raw,
            "trend_state": state,
            "holding_profit_ratio": holding_profit_ratio,
            "trend_break": trend_break,
            "trend_hard_veto": profile.trend_hard_veto,
            "oversold_unlock": oversold_unlock,
            "bear_soft": bool(
                state == "bear"
                and profile.trend_hard_veto
                and profile.bear_soft_mult is not None
                and not oversold_unlock
            ),
            "valuation_p": valuation_p,
        },
    )
