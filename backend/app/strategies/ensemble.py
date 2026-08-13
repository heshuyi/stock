from __future__ import annotations

from app.models import EnsembleResult, StrategyProfile, StrategySignal


STRATEGY_WEIGHTS = {
    "valuation": 0.7,
    "trend": 0.3,
}


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def ensemble(
    *,
    symbol: str,
    name: str,
    etf_code: str,
    target_weight: float,
    signals: list[StrategySignal],
    base_amount: float,
    hard_veto_enabled: bool = True,
    weights: dict[str, float] | None = None,
    profile: StrategyProfile | None = None,
    max_mult: float = 2.0,
) -> EnsembleResult:
    """Merge valuation + trend with weighted average (not multiplication).

    Product rule: valuation sets the primary DCA size; trend only soft-scales.
    Hard veto (valuation pause / growth bear) zeroes the buy — do not encode
    veto as ``m_val * m_trend``. Profit-taking stays out of the buy weights.
    """
    profile = profile or StrategyProfile()
    wmap = weights or profile.strategy_weights or STRATEGY_WEIGHTS
    by_name = {s.strategy: s for s in signals}

    valuation = by_name.get("valuation")
    trend = by_name.get("trend")

    hard_veto = False
    veto_reasons: list[str] = []
    valuation_pause = bool(valuation and valuation.action == "pause")
    valuation_missing = bool(valuation and valuation.meta.get("data_missing"))
    trend_break = bool(trend and trend.meta.get("trend_break"))
    oversold_unlock = bool(trend and trend.meta.get("oversold_unlock"))

    if hard_veto_enabled:
        if valuation_missing and valuation:
            hard_veto = True
            veto_reasons.append(valuation.reason)
        if valuation_pause and valuation and valuation.reason not in veto_reasons:
            hard_veto = True
            veto_reasons.append(valuation.reason)
        # Trend hard veto only when profile opts in and unlock did not fire.
        if (
            profile.trend_hard_veto
            and trend_break
            and not oversold_unlock
            and trend
            and trend.reason not in veto_reasons
        ):
            hard_veto = True
            veto_reasons.append(trend.reason)

    reduce_ratios = [
        s.reduce_ratio for s in signals if s.reduce_ratio is not None
    ]
    max_reduce = max(reduce_ratios) if reduce_ratios else None
    any_reduce = any(s.action == "reduce" for s in signals)

    if oversold_unlock and not hard_veto and trend:
        action = "buy"
        final_mult = float(profile.oversold_mult)
        reason = trend.reason
    elif hard_veto:
        action = "reduce" if any_reduce and max_reduce else "pause"
        final_mult = 0.0
        reason = "硬否决：" + "；".join(veto_reasons)
    else:
        numer = 0.0
        denom = 0.0
        for s in signals:
            w = float(wmap.get(s.strategy, 0.0))
            if w <= 0:
                continue
            numer += w * s.multiplier
            denom += w
        # Explicit weighted average — never m_val * m_trend for buy sizing.
        final_mult = _clip(numer / denom if denom else 0.0, 0.0, max_mult)

        if any_reduce and max_reduce:
            action = "reduce"
            reason = (
                f"加权平均合成 {final_mult:.2f}；同时建议减仓 "
                f"{max_reduce:.0%}"
            )
        elif final_mult <= 1e-9:
            action = "pause" if trend_break else "hold"
            reason = (
                "趋势空头排列，暂停新增" if trend_break else "合成倍数接近 0，观望"
            )
        else:
            action = "buy"
            parts = [
                f"{s.strategy} {float(wmap.get(s.strategy, 0.0)):.0%}×{s.multiplier:.2f}"
                for s in signals
                if float(wmap.get(s.strategy, 0.0)) > 0
            ]
            reason = (
                f"加权平均合成 {final_mult:.2f}（非相乘；{' + '.join(parts)}）"
            )

    amount = (
        round(base_amount * target_weight * final_mult, 2)
        if action == "buy"
        else 0.0
    )

    return EnsembleResult(
        symbol=symbol,
        name=name,
        etf_code=etf_code,
        target_weight=target_weight,
        action=action,
        multiplier=round(final_mult, 4),
        amount=amount,
        reduce_ratio=max_reduce,
        reason=reason,
        strategies=signals,
        hard_veto=hard_veto,
    )


def cash_pool_factor(
    cash: float,
    base_amount: float,
    reserve_months: int = 36,
    *,
    enabled: bool = False,
) -> float:
    """Scale from tracked dry powder; disabled means no cash-pool adjustment."""
    if not enabled or base_amount <= 0 or reserve_months <= 0:
        return 1.0
    target = base_amount * reserve_months
    return _clip(cash / target, 0.35, 1.25)


def apply_cash_pool(
    items: list[EnsembleResult],
    pool_factor: float,
) -> tuple[list[EnsembleResult], bool]:
    """Scale buy amounts exactly by the supplied cash-pool factor."""
    if abs(pool_factor - 1.0) < 1e-9:
        return items, False
    scale = pool_factor
    adjusted: list[EnsembleResult] = []
    changed = False
    for item in items:
        data = item.model_dump()
        if item.action == "buy" and item.amount > 0:
            data["amount"] = round(item.amount * scale, 2)
            data["multiplier"] = round(item.multiplier * scale, 4)
            data["reason"] = item.reason + f"（现金池调节 ×{scale:.2f}）"
            changed = True
        adjusted.append(EnsembleResult.model_validate(data))
    return adjusted, changed


def normalize_amounts(
    items: list[EnsembleResult],
    base_amount: float,
    cap_ratio: float = 1.5,
) -> tuple[list[EnsembleResult], bool]:
    """Scale down buy amounts if total exceeds base_amount * cap_ratio."""
    total = sum(i.amount for i in items if i.action == "buy" and i.amount > 0)
    cap = base_amount * cap_ratio
    if total <= cap or total <= 0:
        return items, False

    scale = cap / total
    scaled: list[EnsembleResult] = []
    for item in items:
        data = item.model_dump()
        if item.amount > 0:
            data["amount"] = round(item.amount * scale, 2)
            data["reason"] = item.reason + f"（已按预算上限缩放 ×{scale:.2f}）"
        scaled.append(EnsembleResult.model_validate(data))
    return scaled, True


def ensure_minimum_investment(
    items: list[EnsembleResult],
    base_amount: float,
    floor_ratio: float = 0.25,
    preferred_symbols: tuple[str, ...] = ("HS300", "ZZ500"),
) -> tuple[list[EnsembleResult], bool]:
    """Keep a small DCA floor when every symbol is paused."""
    if not items or floor_ratio <= 0:
        return items, False
    total = sum(item.amount for item in items if item.amount > 0)
    if total > 0:
        return items, False

    floor_total = round(base_amount * floor_ratio, 2)
    preferred = [item for item in items if item.symbol in preferred_symbols]
    targets = preferred or items[:2]
    if not targets:
        return items, False

    weight_sum = sum(item.target_weight for item in targets) or len(targets)
    adjusted: list[EnsembleResult] = []
    target_ids = {item.symbol for item in targets}
    for item in items:
        data = item.model_dump()
        if item.symbol in target_ids:
            alloc_weight = (
                item.target_weight / weight_sum
                if weight_sum
                else 1.0 / len(targets)
            )
            amount = round(floor_total * alloc_weight, 2)
            multiplier = round(
                amount / max(base_amount * item.target_weight, 1e-9), 4
            )
            data["action"] = "buy"
            data["amount"] = amount
            data["multiplier"] = multiplier
            data["reason"] = item.reason + "；全组合触发暂停时保留底仓定投"
            data["hard_veto"] = False
        adjusted.append(EnsembleResult.model_validate(data))
    return adjusted, True
