from __future__ import annotations

from app.models import StrategySignal


def valuation_signal(
    symbol: str,
    pe_percentile: float | None,
    pb_percentile: float | None,
    pe: float | None = None,
    pb: float | None = None,
    proxy_label: str | None = None,
) -> StrategySignal:
    """Five-year rolling PE percentile → A-share DCA multiplier."""
    source_note = f"{proxy_label}；" if proxy_label else ""
    if pe_percentile is None:
        return StrategySignal(
            strategy="valuation",
            symbol=symbol,
            action="pause",
            multiplier=0.0,
            confidence=0.0,
            reason=f"{source_note}同口径 PE 数据缺失，暂停新增",
            meta={
                "pe": pe,
                "pb": pb,
                "pe_percentile": pe_percentile,
                "pb_percentile": pb_percentile,
                "proxy_label": proxy_label,
                "data_missing": True,
            },
        )

    p = pe_percentile
    high_pause = 0.80
    if p < 0.20:
        mult, action, reason = 2.0, "buy", f"{source_note}近5年 PE 分位 {p:.0%} <20%，极度低估双倍定投"
    elif p < 0.40:
        mult, action, reason = 1.5, "buy", f"{source_note}近5年 PE 分位 {p:.0%}，偏低估值加码"
    elif p < 0.60:
        mult, action, reason = 1.0, "buy", f"{source_note}近5年 PE 分位 {p:.0%}，合理估值标准定投"
    elif p < 0.80:
        mult, action, reason = 0.5, "buy", f"{source_note}近5年 PE 分位 {p:.0%}，偏高估值减半定投"
    else:
        mult, action, reason = 0.0, "pause", f"{source_note}近5年 PE 分位 {p:.0%} ≥80%，停止定投"

    return StrategySignal(
        strategy="valuation",
        symbol=symbol,
        action=action,
        multiplier=mult,
        confidence=0.9,
        reason=reason,
        meta={
            "pe": pe,
            "pb": pb,
            "pe_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
            "p": p,
            "proxy_label": proxy_label,
            "pause_threshold": high_pause,
            "data_missing": False,
        },
    )
