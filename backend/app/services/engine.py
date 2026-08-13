from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from app.db import (
    get_db,
    get_portfolio,
    get_user_settings,
    load_app_config,
    save_portfolio,
)
from app.models import DashboardResponse, EnsembleResult, Holding, Portfolio
from app.services.market_data import (
    get_signal_market,
    valuation_lag_sessions,
    warm_latest_snapshots,
)
from app.services import market_store
from app.services.schedule import (
    execution_calendar,
    is_execution_day,
    next_execution_date,
    period_amount,
    TradingCalendarUnavailable,
)
from app.strategies.ensemble import (
    apply_cash_pool,
    cash_pool_factor,
    ensemble,
    ensure_minimum_investment,
    normalize_amounts,
)
from app.strategies.profit_taking import profit_taking_signal
from app.strategies.trend import trend_signal
from app.strategies.valuation import valuation_signal


def _holding_map(holdings: list[Holding]) -> dict[str, Holding]:
    return {h.symbol: h for h in holdings}


def _strategy_state_changed(before: list[Holding], after: list[Holding]) -> bool:
    """True when auto-derived trend/trailing fields differ (not shares/cost)."""
    before_map = {h.symbol: h for h in before}
    for h in after:
        prev = before_map.get(h.symbol)
        if prev is None:
            if h.trend_state or h.trailing_armed or h.trail_peak_price is not None:
                return True
            continue
        if prev.trend_state != h.trend_state:
            return True
        if prev.trailing_armed != h.trailing_armed:
            return True
        if prev.trail_peak_price != h.trail_peak_price:
            return True
    return False


def _clamp_signal_date(as_of: str | None) -> tuple[str | None, str]:
    """Return (signal_date, as_of_mode). Always clamp to <= T-1 warehouse date."""
    t1 = market_store.resolve_signal_date()
    if not as_of:
        return t1, "T-1"
    if not t1:
        return None, "T-1"
    clamped = min(as_of, t1)
    mode = "T-1" if clamped == t1 and as_of >= t1 else "historical"
    return clamped, mode


def _suppress_buys_for_non_execution(
    items: list[EnsembleResult],
) -> list[EnsembleResult]:
    """Zero buy amounts off schedule; keep reduce/pause signals."""
    out: list[EnsembleResult] = []
    for item in items:
        if item.action == "buy" and item.amount > 0:
            data = item.model_dump()
            data["action"] = "hold"
            data["amount"] = 0.0
            data["reason"] = "非定投执行日，仅观察；" + item.reason
            out.append(EnsembleResult.model_validate(data))
        else:
            out.append(item)
    return out


async def compute_dashboard(as_of: str | None = None) -> DashboardResponse:
    cfg = load_app_config()
    settings = await get_user_settings()
    portfolio = await get_portfolio()
    hmap = _holding_map(portfolio.holdings)

    target_weights = settings.target_weights or {
        s.id: s.target_weight for s in cfg.symbols
    }

    warning = None
    await warm_latest_snapshots()

    signal_date, as_of_mode = _clamp_signal_date(as_of)
    execution_day = date.today()
    warehouse_days = market_store.list_trading_dates(
        start=f"{execution_day.year - 1}-01-01",
        end=f"{execution_day.year + 1}-12-31",
    )
    latest_bar: date | None = None
    if signal_date:
        latest_bar = date.fromisoformat(str(signal_date)[:10])
    month_end = date(
        execution_day.year,
        execution_day.month,
        monthrange(execution_day.year, execution_day.month)[1],
    )
    try:
        trading_days = execution_calendar(
            warehouse_days,
            today=execution_day,
            latest_bar=latest_bar,
            until=month_end,
        )
        p_amount = period_amount(
            settings.base_amount,
            settings.buy_frequency,
            year=execution_day.year,
            month=execution_day.month,
            trading_days=trading_days,
        )
        exec_today = is_execution_day(
            execution_day,
            settings.buy_frequency,
            weekly_weekday=settings.weekly_weekday,
            monthly_day=settings.monthly_day,
            trading_days=trading_days,
            latest_bar=latest_bar,
        )
        next_exec = next_execution_date(
            execution_day,
            settings.buy_frequency,
            weekly_weekday=settings.weekly_weekday,
            monthly_day=settings.monthly_day,
            warehouse_days=warehouse_days,
            latest_bar=latest_bar,
        )
    except TradingCalendarUnavailable as exc:
        return DashboardResponse(
            date=signal_date or as_of or "",
            base_amount=settings.base_amount,
            period_amount=0,
            buy_frequency=settings.buy_frequency,
            execution_today=False,
            next_execution_date=None,
            total_buy_amount=0,
            normalized=False,
            items=[],
            warning=f"{exc}；为避免错误分配，本期额度已暂停",
        )

    sample = (
        await get_signal_market(cfg.symbols[0].id, signal_date) if signal_date else None
    )

    if not sample or not signal_date:
        warning = "暂无行情数据，请点击「同步行情」入库（首次约 1–2 分钟）"
        return DashboardResponse(
            date=as_of or "",
            base_amount=settings.base_amount,
            period_amount=p_amount,
            buy_frequency=settings.buy_frequency,
            execution_today=exec_today,
            next_execution_date=next_exec,
            total_buy_amount=0,
            normalized=False,
            items=[],
            warning=warning,
        )

    if as_of and as_of > signal_date:
        warning = f"请求日期 {as_of} 超出可用 T-1，已钳制为 {signal_date}"
    elif sample.get("source") == "mock":
        warning = "当前为 mock 数据，请点击「同步行情」切换为实时行情"
    elif as_of_mode == "historical":
        warning = f"历史回看信号日 {signal_date}（已钳制不超过 T-1）"
    else:
        warning = f"信号基于前一交易日 {signal_date}（T-1）收盘数据"

    freq_label = {"daily": "每日", "weekly": "每周", "monthly": "每月"}[
        settings.buy_frequency
    ]
    if exec_today:
        warning = (
            (warning + "；" if warning else "")
            + f"今日为{freq_label}定投执行日，本期基准 ¥{p_amount:,.2f}"
        )
    else:
        warning = (
            (warning + "；" if warning else "")
            + f"今日非{freq_label}定投执行日，分配额度为 0"
            + (f"，下一执行日 {next_exec}" if next_exec else "")
        )

    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for sym in cfg.symbols:
        bar = await get_signal_market(sym.id, signal_date)
        if not bar:
            continue
        latest_by_symbol[sym.id] = bar

    pool_factor = cash_pool_factor(
        portfolio.cash,
        settings.base_amount,
        cfg.defaults.cash_reserve_months,
    )
    max_mult = 2.0 if pool_factor >= 1.0 else 1.8
    cap_ratio = 2.0 if pool_factor >= 1.0 else settings.normalize_buy_cap

    items: list[EnsembleResult] = []
    updated_holdings: list[Holding] = []
    holdings_by_id = {h.symbol: h for h in portfolio.holdings}
    valuation_issues: list[str] = []

    for sym in cfg.symbols:
        latest = latest_by_symbol.get(sym.id)
        if not latest:
            continue

        profile = sym.strategy_profile
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
        mark_price = (
            float(latest["etf_close"])
            if latest.get("etf_close") is not None
            else price
        )

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
        valuation_p = None
        if sym.valuation_enabled:
            valuation_asof = latest.get("valuation_asof")
            try:
                valuation_lag = valuation_lag_sessions(
                    valuation_asof, signal_date
                )
            except TradingCalendarUnavailable:
                valuation_lag = None
            s_val = valuation_signal(
                sym.id,
                pe_p,
                pb_p,
                pe=pe,
                pb=pb,
                proxy_label=(
                    sym.valuation_proxy_label if sym.valuation_proxy else None
                ),
                profile=profile,
                valuation_asof=valuation_asof,
                valuation_lag_sessions=valuation_lag,
            )
            strategy_signals.append(s_val)
            if s_val.meta.get("data_missing"):
                valuation_issues.append(sym.name)
            raw_p = s_val.meta.get("p")
            valuation_p = float(raw_p) if raw_p is not None else None

        s_trend = trend_signal(
            sym.id,
            price,
            ma_s,
            ma_l,
            profit_ratio,
            profile=profile,
            prev_state=holding.trend_state if holding else None,
            valuation_p=valuation_p,
        )
        strategy_signals.append(s_trend)

        s_profit = profit_taking_signal(
            sym.id,
            valuation_p=valuation_p,
            price=mark_price,
            has_position=bool(holding and holding.shares > 0),
            current_stage=holding.take_profit_stage if holding else 0,
            trailing_armed=bool(holding.trailing_armed) if holding else False,
            trail_peak_price=holding.trail_peak_price if holding else None,
            valuation_enabled=sym.valuation_enabled,
            enabled=settings.profit_take_enabled,
            profile=profile.model_copy(
                update={
                    "trail_arm_percentile": settings.valuation_reduce_percentile,
                    "trail_exit_percentile": settings.valuation_exit_percentile,
                }
            ),
            pe_percentile=pe_p,
            profit_ratio=profit_ratio,
        )
        strategy_signals.append(s_profit)

        result = ensemble(
            symbol=sym.id,
            name=sym.name,
            etf_code=sym.etf_code,
            target_weight=tw,
            signals=strategy_signals,
            base_amount=p_amount,
            hard_veto_enabled=settings.hard_veto_enabled,
            weights=profile.strategy_weights,
            profile=profile,
            max_mult=max_mult,
        )
        items.append(result)

        base_h = holdings_by_id.get(sym.id) or Holding(symbol=sym.id)
        trend_state = s_trend.meta.get("trend_state")
        updated_holdings.append(
            Holding(
                symbol=sym.id,
                shares=base_h.shares,
                cost_price=base_h.cost_price,
                market_value=base_h.market_value,
                take_profit_stage=base_h.take_profit_stage,
                trend_state=trend_state,
                trailing_armed=bool(s_profit.meta.get("trailing_armed")),
                trail_peak_price=s_profit.meta.get("trail_peak_price"),
            )
        )

    if valuation_issues:
        warning = (
            (warning + "；" if warning else "")
            + f"{'、'.join(valuation_issues)}估值过期或缺失，已安全暂停新增"
        )

    known = {h.symbol for h in updated_holdings}
    for h in portfolio.holdings:
        if h.symbol not in known:
            updated_holdings.append(h)
    if _strategy_state_changed(portfolio.holdings, updated_holdings):
        await save_portfolio(
            Portfolio(holdings=updated_holdings, cash=portfolio.cash)
        )

    if valuation_issues:
        floor_applied = False
    else:
        items, floor_applied = ensure_minimum_investment(
            items, p_amount, cfg.defaults.minimum_invest_ratio
        )
    if floor_applied:
        warning = (
            (warning + "；" if warning else "")
            + f"全组合原始信号为暂停，按底仓纪律保留 {cfg.defaults.minimum_invest_ratio:.0%} 定投"
        )

    items, pool_applied = apply_cash_pool(items, pool_factor)
    if pool_applied:
        warning = (
            (warning + "；" if warning else "")
            + f"现金池调节系数 {pool_factor:.2f}"
        )
        if pool_factor < 0.5:
            warning += "（弹药偏薄，已额外降速）"

    items, normalized = normalize_amounts(items, p_amount, cap_ratio)

    if not exec_today:
        items = _suppress_buys_for_non_execution(items)

    total_buy = sum(i.amount for i in items if i.action == "buy")

    db = get_db()
    payload = {
        "date": signal_date,
        "base_amount": settings.base_amount,
        "period_amount": p_amount,
        "buy_frequency": settings.buy_frequency,
        "execution_today": exec_today,
        "next_execution_date": next_exec,
        "total_buy_amount": total_buy,
        "normalized": normalized,
        "items": [i.model_dump() for i in items],
        "warning": warning,
        "as_of_mode": as_of_mode,
        "pool_factor": pool_factor,
    }
    await db.signals_daily.update_one(
        {"date": signal_date},
        {"$set": payload},
        upsert=True,
    )

    return DashboardResponse(
        date=signal_date,
        base_amount=settings.base_amount,
        period_amount=p_amount,
        buy_frequency=settings.buy_frequency,
        execution_today=exec_today,
        next_execution_date=next_exec,
        total_buy_amount=round(total_buy, 2),
        normalized=normalized,
        items=items,
        warning=warning,
        pool_factor=pool_factor,
    )
