from __future__ import annotations

from app.models import EnsembleResult, StrategyProfile, StrategySignal


STRATEGY_WEIGHTS = {
    "valuation": 0.7,
    "trend": 0.3,
}


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _theoretical_max(
    weights: dict[str, float], profile: StrategyProfile
) -> float:
    """Highest multiplier the weighted average can reach given the profile.

    Weighted average of (valuation max tier) and (trend bull) at their maxes —
    this is the ceiling the configured ``max_mult`` must cover to be reachable.
    """
    w_val = float(weights.get("valuation", 0.0))
    w_trend = float(weights.get("trend", 0.0))
    max_val = max(profile.tier_mults) if profile.tier_mults else 1.0
    max_trend = max(float(v) for v in profile.trend_mults.model_dump().values())
    return w_val * max_val + w_trend * max_trend


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
    block_reason: str | None = None,
) -> EnsembleResult:
    """Merge valuation + trend with weighted average (not multiplication).

    Product rule: valuation sets the primary DCA size; trend only soft-scales.
    Hard veto (valuation pause / growth bear) zeroes the buy — do not encode
    veto as ``m_val * m_trend``. Profit-taking stays out of the buy weights.

    ``block_reason`` is an external buy lock (e.g. profit-taking full-exit
    stage still hot) — it vetoes new buys but never overrides a reduce signal.
    When ``profile.scale_to_cap`` and the configured ``max_mult`` is higher
    than the reachable weighted-average ceiling, the average is rescaled so
    the cap genuinely binds at full undervaluation + bull trend.
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

    if block_reason:
        hard_veto = True
        veto_reasons.append(block_reason)
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

        scale_note = ""
        if profile.scale_to_cap and denom > 0:
            ceiling = _theoretical_max(wmap, profile)
            if ceiling > 1e-9 and max_mult > ceiling * (1 + 1e-9):
                scale = max_mult / ceiling
                final_mult = _clip(final_mult * scale, 0.0, max_mult)
                scale_note = f"（按角色可达上限缩放 ×{scale:.3f}）"

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
                f"{scale_note}"
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
    """Scale from tracked dry powder; disabled means no cash-pool adjustment.

    Empty dry powder (``cash == 0``) yields factor 0 — the pool is a real
    ammunition gauge, so an empty pool stops new buys entirely.
    """
    if not enabled or base_amount <= 0 or reserve_months <= 0:
        return 1.0
    target = base_amount * reserve_months
    return _clip(cash / target, 0.0, 1.25)


def apply_cash_pool(
    items: list[EnsembleResult],
    pool_factor: float,
) -> tuple[list[EnsembleResult], bool]:
    """Scale buy amounts exactly by the supplied cash-pool factor."""
    if abs(pool_factor - 1.0) < 1e-9:
        return items, False
    if pool_factor < 1e-9:
        # Empty dry powder: no new buys, keep reduce/hold signals untouched.
        adjusted: list[EnsembleResult] = []
        for item in items:
            if item.action == "buy" and item.amount > 0:
                adjusted.append(
                    item.model_copy(
                        update={
                            "action": "hold",
                            "amount": 0.0,
                            "multiplier": 0.0,
                            "reason": item.reason + "（现金池为 0，弹药为空，本期暂停买入）",
                        }
                    )
                )
            else:
                adjusted.append(item)
        return adjusted, True
    scale = pool_factor
    adjusted = []
    changed = False
    for item in items:
        if item.action == "buy" and item.amount > 0:
            adjusted.append(
                item.model_copy(
                    update={
                        "amount": round(item.amount * scale, 2),
                        "multiplier": round(item.multiplier * scale, 4),
                        "reason": item.reason + f"（现金池调节 ×{scale:.2f}）",
                    }
                )
            )
            changed = True
        else:
            adjusted.append(item)
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
        if item.amount > 0:
            scaled.append(
                item.model_copy(
                    update={
                        "amount": round(item.amount * scale, 2),
                        "reason": item.reason + f"（已按预算上限缩放 ×{scale:.2f}）",
                    }
                )
            )
        else:
            scaled.append(item)
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
            adjusted.append(
                item.model_copy(
                    update={
                        "action": "buy",
                        "amount": amount,
                        "multiplier": multiplier,
                        "reason": item.reason + "；全组合触发暂停时保留底仓定投",
                        "hard_veto": False,
                    }
                )
            )
        else:
            adjusted.append(item)
    return adjusted, True
