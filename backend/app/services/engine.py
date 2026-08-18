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
from app.services.market_data import get_signal_market
from app.services import market_store
from app.services.schedule import (
    execution_calendar,
    is_execution_day,
    next_execution_date,
    period_amount,
    TradingCalendarUnavailable,
)
from app.services.strategy_pipeline import (
    PipelineInputs,
    apply_growth_bear_policy,
    run_strategy_pipeline,
)
from app.strategies.ensemble import (
    apply_cash_pool,
    cash_pool_factor,
    ensure_minimum_investment,
    normalize_amounts,
)


def _strategy_state_changed(before: list[Holding], after: list[Holding]) -> bool:
    """True when auto-derived trend/profit-taking fields differ."""
    before_map = {h.symbol: h for h in before}
    for h in after:
        prev = before_map.get(h.symbol)
        if prev is None:
            if h.trend_state or h.trailing_armed or h.trail_peak_price is not None:
                return True
            continue
        if prev.trend_state != h.trend_state:
            return True
        if prev.take_profit_stage != h.take_profit_stage:
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
            out.append(
                item.model_copy(
                    update={
                        "action": "hold",
                        "amount": 0.0,
                        "reason": "非定投执行日，仅观察；" + item.reason,
                    }
                )
            )
        else:
            out.append(item)
    return out


async def compute_dashboard(as_of: str | None = None) -> DashboardResponse:
    cfg = load_app_config()
    settings = await get_user_settings()
    portfolio = await get_portfolio()
    target_weights = settings.target_weights or {
        s.id: s.target_weight for s in cfg.symbols
    }

    warning = None

    signal_date, as_of_mode = _clamp_signal_date(as_of)
    execution_day = date.today()
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
            trading_days=trading_days,
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
        enabled=settings.cash_pool_enabled,
    )
    max_mult = 2.0 if pool_factor >= 1.0 else 1.8
    cap_ratio = 2.0 if pool_factor >= 1.0 else settings.normalize_buy_cap

    pipeline = run_strategy_pipeline(
        PipelineInputs(
            symbols=apply_growth_bear_policy(
                cfg.symbols,
                settings.growth_bear_policy,
                settings.growth_bear_mult,
            ),
            latest_by_symbol=latest_by_symbol,
            signal_date=signal_date,
            base_amount=p_amount,
            target_weights=target_weights,
            holdings=portfolio.holdings,
            hard_veto_enabled=settings.hard_veto_enabled,
            profit_take_enabled=settings.profit_take_enabled,
            valuation_reduce_percentile=settings.valuation_reduce_percentile,
            valuation_exit_percentile=settings.valuation_exit_percentile,
            max_mult=max_mult,
        )
    )
    items = pipeline.items
    updated_holdings = pipeline.updated_holdings
    valuation_issues = pipeline.valuation_issues

    if valuation_issues:
        warning = (
            (warning + "；" if warning else "")
            + f"{'、'.join(valuation_issues)}估值过期或缺失，已安全暂停新增"
        )
    if pipeline.missing_symbols:
        warning = (
            (warning + "；" if warning else "")
            + f"以下标的无行情数据，未生成信号：{'、'.join(pipeline.missing_symbols)}"
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
