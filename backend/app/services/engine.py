from __future__ import annotations

from datetime import date
from typing import Any

from app.db import (
    get_db,
    get_portfolio,
    get_user_settings,
    load_app_config,
)
from app.models import DashboardResponse, EnsembleResult, Holding
from app.services.market_data import get_signal_market, warm_latest_snapshots
from app.services import market_store
from app.strategies.ensemble import (
    ensemble,
    ensure_minimum_investment,
    normalize_amounts,
)
from app.strategies.profit_taking import profit_taking_signal
from app.strategies.trend import trend_signal
from app.strategies.valuation import valuation_signal


def _holding_map(holdings: list[Holding]) -> dict[str, Holding]:
    return {h.symbol: h for h in holdings}


def _clamp_signal_date(as_of: str | None) -> tuple[str | None, str]:
    """Return (signal_date, as_of_mode). Always clamp to <= T-1 warehouse date."""
    t1 = market_store.resolve_signal_date()
    if not as_of:
        return t1, "T-1"
    if not t1:
        return None, "T-1"
    # Never use today/future/unclosed bars for decisions
    clamped = min(as_of, t1)
    mode = "T-1" if clamped == t1 and as_of >= t1 else "historical"
    return clamped, mode


def _is_weekly_execution_day(
    signal_date: str, execution_day: date | None = None
) -> bool:
    """Monday or the first trading day after a Monday holiday.

    Before that session opens, the latest completed signal still belongs to
    the previous ISO week. On later weekdays, T-1 is already in this week.
    """
    execution_day = execution_day or date.today()
    signal_week = date.fromisoformat(signal_date).isocalendar()[:2]
    execution_week = execution_day.isocalendar()[:2]
    return signal_week != execution_week


async def compute_dashboard(as_of: str | None = None) -> DashboardResponse:
    cfg = load_app_config()
    settings = await get_user_settings()
    portfolio = await get_portfolio()
    hmap = _holding_map(portfolio.holdings)

    target_weights = settings.target_weights or {
        s.id: s.target_weight for s in cfg.symbols
    }

    warning = None
    # Light warm: latest bar only (no full-history load on every request)
    await warm_latest_snapshots()

    signal_date, as_of_mode = _clamp_signal_date(as_of)
    sample = (
        await get_signal_market(cfg.symbols[0].id, signal_date) if signal_date else None
    )

    if not sample or not signal_date:
        warning = "暂无行情数据，请点击「同步行情」入库（首次约 1–2 分钟）"
        return DashboardResponse(
            date=as_of or "",
            base_amount=settings.base_amount,
            total_buy_amount=0,
            normalized=False,
            items=[],
            warning=warning,
        )

    if as_of and as_of > signal_date:
        warning = (
            f"请求日期 {as_of} 超出可用 T-1，已钳制为 {signal_date}"
        )
    elif sample.get("source") == "mock":
        warning = "当前为 mock 数据，请点击「同步行情」切换为实时行情"
    elif as_of_mode == "historical":
        warning = f"历史回看信号日 {signal_date}（已钳制不超过 T-1）"
    else:
        warning = f"信号基于前一交易日 {signal_date}（T-1）收盘数据"

    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for sym in cfg.symbols:
        bar = await get_signal_market(sym.id, signal_date)
        if not bar:
            continue
        latest_by_symbol[sym.id] = bar

    items: list[EnsembleResult] = []
    weekly_execution = True if as_of else _is_weekly_execution_day(signal_date)
    if not weekly_execution:
        warning = (
            (warning + "；" if warning else "")
            + "今天不是本周首个交易日，买入信号仅观察，止盈信号仍执行"
        )
    for sym in cfg.symbols:
        latest = latest_by_symbol.get(sym.id)
        if not latest:
            continue

        tw = float(target_weights.get(sym.id, sym.target_weight))
        pe_p_raw = latest.get("pe_percentile")
        pb_p_raw = latest.get("pb_percentile")
        pe_raw = latest.get("pe")
        pb_raw = latest.get("pb")
        pe_p = float(pe_p_raw) if pe_p_raw is not None else None
        pb_p = float(pb_p_raw) if pb_p_raw is not None else None
        pe = float(pe_raw) if pe_raw is not None else None
        pb = float(pb_raw) if pb_raw is not None else None
        price = float(latest["close"])
        ma_s = float(latest.get("ma_short") or price)
        ma_l = float(latest.get("ma_long") or price)

        holding = hmap.get(sym.id)
        profit_ratio = None
        if holding and holding.shares > 0 and holding.cost_price > 0:
            if holding.market_value is not None:
                holding_price = holding.market_value / holding.shares
            elif latest.get("etf_close") is not None:
                holding_price = float(latest["etf_close"])
            else:
                holding_price = None
            if holding_price is not None:
                profit_ratio = (
                    holding_price - holding.cost_price
                ) / holding.cost_price

        strategy_signals = []
        if sym.valuation_enabled:
            strategy_signals.append(
                valuation_signal(
                    sym.id,
                    pe_p,
                    pb_p,
                    pe=pe,
                    pb=pb,
                    proxy_label=(
                        sym.valuation_proxy_label if sym.valuation_proxy else None
                    ),
                )
            )
        s_trend = trend_signal(sym.id, price, ma_s, ma_l, profit_ratio)
        strategy_signals.append(s_trend)
        strategy_signals.append(
            profit_taking_signal(
                sym.id,
                pe_percentile=pe_p,
                profit_ratio=profit_ratio,
                has_position=bool(holding and holding.shares > 0),
                current_stage=holding.take_profit_stage if holding else 0,
                valuation_enabled=sym.valuation_enabled,
                enabled=settings.profit_take_enabled,
                profit_threshold=settings.profit_take_return,
                valuation_reduce_threshold=settings.valuation_reduce_percentile,
                valuation_exit_threshold=settings.valuation_exit_percentile,
            )
        )

        result = ensemble(
            symbol=sym.id,
            name=sym.name,
            etf_code=sym.etf_code,
            target_weight=tw,
            signals=strategy_signals,
            base_amount=settings.base_amount,
            hard_veto_enabled=settings.hard_veto_enabled,
            weights=cfg.defaults.strategy_weights,
        )
        if result.action == "buy" and not weekly_execution:
            data = result.model_dump()
            data["action"] = "hold"
            data["amount"] = 0.0
            data["reason"] = "非本周首个交易日，仅观察；" + result.reason
            result = EnsembleResult.model_validate(data)
        items.append(result)

    items, floor_applied = ensure_minimum_investment(
        items, settings.base_amount, cfg.defaults.minimum_invest_ratio
    )
    if floor_applied:
        warning = (
            (warning + "；" if warning else "")
            + f"全组合原始信号为暂停，按底仓纪律保留 {cfg.defaults.minimum_invest_ratio:.0%} 定投"
        )
    items, normalized = normalize_amounts(
        items, settings.base_amount, settings.normalize_buy_cap
    )
    total_buy = sum(i.amount for i in items if i.action == "buy")

    db = get_db()
    payload = {
        "date": signal_date,
        "base_amount": settings.base_amount,
        "total_buy_amount": total_buy,
        "normalized": normalized,
        "items": [i.model_dump() for i in items],
        "warning": warning,
        "as_of_mode": as_of_mode,
    }
    await db.signals_daily.update_one(
        {"date": signal_date},
        {"$set": payload},
        upsert=True,
    )

    return DashboardResponse(
        date=signal_date,
        base_amount=settings.base_amount,
        total_buy_amount=round(total_buy, 2),
        normalized=normalized,
        items=items,
        warning=warning,
    )
