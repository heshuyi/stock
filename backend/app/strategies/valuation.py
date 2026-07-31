from __future__ import annotations

from app.models import StrategyProfile, StrategySignal


def _compose_percentile(
    pe_percentile: float | None,
    pb_percentile: float | None,
    profile: StrategyProfile,
) -> tuple[float | None, str, bool]:
    """Return (p, mode_note, data_missing)."""
    if profile.valuation_mode == "pe_pb_composite":
        pe_w = float(profile.pe_weight)
        pb_w = float(profile.pb_weight)
        if pe_percentile is not None and pb_percentile is not None:
            p = pe_w * pe_percentile + pb_w * pb_percentile
            note = f"复合估值 {pe_w:.0%}PE+{pb_w:.0%}PB"
            return p, note, False
        if pe_percentile is not None:
            return pe_percentile, "复合估值缺 PB，回退 PE", False
        if pb_percentile is not None:
            return pb_percentile, "复合估值缺 PE，回退 PB", False
        return None, "复合估值 PE/PB 均缺失", True

    # PE-primary; PB only when PE missing
    if pe_percentile is not None:
        return pe_percentile, "PE 分位", False
    if pb_percentile is not None:
        return pb_percentile, "PE 缺失，回退 PB 分位", False
    return None, "PE/PB 分位均缺失", True


def valuation_signal(
    symbol: str,
    pe_percentile: float | None,
    pb_percentile: float | None,
    pe: float | None = None,
    pb: float | None = None,
    proxy_label: str | None = None,
    profile: StrategyProfile | None = None,
) -> StrategySignal:
    """Role-aware valuation DCA multiplier from percentile bands."""
    profile = profile or StrategyProfile()
    source_note = f"{proxy_label}；" if proxy_label else ""
    window_note = (
        "全样本滚动分位"
        if profile.percentile_window == "full"
        else "近5年滚动分位"
    )
    p, mode_note, missing = _compose_percentile(
        pe_percentile, pb_percentile, profile
    )
    if missing or p is None:
        return StrategySignal(
            strategy="valuation",
            symbol=symbol,
            action="pause",
            multiplier=0.0,
            confidence=0.0,
            reason=f"{source_note}{mode_note}，暂停新增",
            meta={
                "pe": pe,
                "pb": pb,
                "pe_percentile": pe_percentile,
                "pb_percentile": pb_percentile,
                "proxy_label": proxy_label,
                "valuation_mode": profile.valuation_mode,
                "percentile_window": profile.percentile_window,
                "data_missing": True,
            },
        )

    pause = float(profile.pause_percentile)
    tiers = list(profile.tier_mults) + [0.0]
    while len(tiers) < 5:
        tiers.append(0.0)
    band_note = f"{source_note}{mode_note}（{window_note}）p={p:.0%}"

    if p < 0.20:
        mult, action, reason = tiers[0], "buy", f"{band_note} <20%，低估加码"
    elif p < 0.40:
        mult, action, reason = tiers[1], "buy", f"{band_note}，偏低估值加码"
    elif p < 0.60:
        mult, action, reason = tiers[2], "buy", f"{band_note}，合理估值标准定投"
    elif p < pause:
        mult, action, reason = tiers[3], "buy", f"{band_note}，偏高估值减额定投"
    else:
        mult, action, reason = (
            0.0,
            "pause",
            f"{band_note} ≥{pause:.0%}，停止定投",
        )

    return StrategySignal(
        strategy="valuation",
        symbol=symbol,
        action=action,
        multiplier=float(mult),
        confidence=0.9,
        reason=reason,
        meta={
            "pe": pe,
            "pb": pb,
            "pe_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
            "p": p,
            "proxy_label": proxy_label,
            "pause_threshold": pause,
            "valuation_mode": profile.valuation_mode,
            "percentile_window": profile.percentile_window,
            "data_missing": False,
        },
    )
