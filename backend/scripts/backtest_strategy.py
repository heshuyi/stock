#!/usr/bin/env python3
"""Point-in-time offline comparison of the strategy and equal-weight DCA."""

from __future__ import annotations

import argparse
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from app.db import load_app_config
from app.models import SymbolConfig
from app.services import market_store
from app.strategies.ensemble import ensemble, normalize_amounts
from app.strategies.profit_taking import profit_taking_signal
from app.strategies.trend import trend_signal
from app.strategies.valuation import valuation_signal


@dataclass
class Account:
    cash: float = 0.0
    shares: dict[str, float] = field(default_factory=dict)
    stages: dict[str, int] = field(default_factory=dict)
    armed: dict[str, bool] = field(default_factory=dict)
    peaks: dict[str, float | None] = field(default_factory=dict)
    fees: float = 0.0


def xirr(cashflows: Iterable[tuple[date, float]]) -> float | None:
    flows = list(cashflows)
    if not flows or not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    origin = flows[0][0]

    def npv(rate: float) -> float:
        return sum(
            value / ((1 + rate) ** ((when - origin).days / 365.0))
            for when, value in flows
        )

    low, high = -0.9999, 10.0
    while npv(low) * npv(high) > 0 and high < 1_000_000:
        high *= 2
    if npv(low) * npv(high) > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        if npv(low) * npv(mid) <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def max_drawdown(values: Iterable[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def calculate_metrics(
    dates: list[date],
    values: list[float],
    contributions: list[float],
    total_invested: float,
) -> dict[str, float | None]:
    returns: list[float] = []
    for previous, current, contribution in zip(
        values, values[1:], contributions[1:]
    ):
        if previous > 0:
            returns.append((current - contribution) / previous - 1)
    twr = math.prod(1 + value for value in returns) - 1 if returns else 0.0
    volatility = (
        statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    )
    flows = [(when, -amount) for when, amount in zip(dates, contributions) if amount]
    if dates:
        flows.append((dates[-1], values[-1]))
    return {
        "total_invested": total_invested,
        "ending_value": values[-1] if values else 0.0,
        "xirr": xirr(flows),
        "twr": twr,
        "max_drawdown": max_drawdown(values),
        "volatility": volatility,
    }


def _execution_dates(
    dates: list[str], frequency: str, weekday: int, monthday: int
) -> set[str]:
    if frequency == "daily":
        return set(dates)
    grouped: dict[tuple[int, int], list[str]] = {}
    for value in dates:
        day = date.fromisoformat(value)
        key = (
            (day.isocalendar().year, day.isocalendar().week)
            if frequency == "weekly"
            else (day.year, day.month)
        )
        grouped.setdefault(key, []).append(value)
    selected: set[str] = set()
    for group in grouped.values():
        target = [
            value
            for value in group
            if (
                date.fromisoformat(value).isoweekday() >= weekday
                if frequency == "weekly"
                else date.fromisoformat(value).day >= monthday
            )
        ]
        selected.add((target or group[-1:])[0])
    return selected


def _period_amounts(dates: list[str], execution: set[str], monthly: float) -> dict[str, float]:
    counts: dict[str, int] = {}
    for value in execution:
        counts[value[:7]] = counts.get(value[:7], 0) + 1
    return {value: monthly / counts[value[:7]] for value in execution}


def _price(row: dict[str, object] | None) -> float | None:
    if not row:
        return None
    raw = row.get("etf_close")
    return float(raw) if raw is not None and float(raw) > 0 else None


def _value(account: Account, rows: dict[str, dict[str, object]]) -> float:
    return account.cash + sum(
        shares * (_price(rows.get(symbol)) or 0.0)
        for symbol, shares in account.shares.items()
    )


def _buy(account: Account, symbol: str, amount: float, price: float, fee: float) -> None:
    spend = min(amount, account.cash)
    if spend <= 0:
        return
    charge = spend * fee
    account.fees += charge
    account.shares[symbol] = account.shares.get(symbol, 0.0) + (
        (spend - charge) / price
    )
    account.cash -= spend


def _sell(account: Account, symbol: str, ratio: float, price: float, fee: float) -> None:
    quantity = account.shares.get(symbol, 0.0) * ratio
    if quantity <= 0:
        return
    gross = quantity * price
    charge = gross * fee
    account.fees += charge
    account.cash += gross - charge
    account.shares[symbol] -= quantity


def _strategy_orders(
    symbols: list[SymbolConfig],
    signal_rows: dict[str, dict[str, object]],
    account: Account,
    amount: float,
) -> tuple[list[tuple[str, float]], list[tuple[str, float, int]], int]:
    results = []
    reductions: list[tuple[str, float, int]] = []
    pauses = 0
    for symbol in symbols:
        row = signal_rows.get(symbol.id)
        if not row:
            pauses += 1
            continue
        mark = _price(row)
        valuation = valuation_signal(
            symbol.id,
            row.get("pe_percentile"),
            row.get("pb_percentile"),
            pe=row.get("pe"),
            pb=row.get("pb"),
            profile=symbol.strategy_profile,
        )
        p = valuation.meta.get("p")
        trend = trend_signal(
            symbol.id,
            float(row["close"]),
            float(row.get("ma_short") or row["close"]),
            float(row.get("ma_long") or row["close"]),
            profile=symbol.strategy_profile,
            valuation_p=float(p) if p is not None else None,
        )
        profit = profit_taking_signal(
            symbol.id,
            valuation_p=float(p) if p is not None else None,
            price=mark,
            has_position=account.shares.get(symbol.id, 0) > 0,
            current_stage=account.stages.get(symbol.id, 0),
            trailing_armed=account.armed.get(symbol.id, False),
            trail_peak_price=account.peaks.get(symbol.id),
            profile=symbol.strategy_profile,
        )
        account.armed[symbol.id] = bool(profit.meta["trailing_armed"])
        account.peaks[symbol.id] = profit.meta["trail_peak_price"]
        if account.shares.get(symbol.id, 0) <= 0:
            account.stages[symbol.id] = 0
        if profit.reduce_ratio:
            reductions.append(
                (
                    symbol.id,
                    float(profit.reduce_ratio),
                    int(profit.meta["recommended_stage"]),
                )
            )
        result = ensemble(
            symbol=symbol.id,
            name=symbol.name,
            etf_code=symbol.etf_code,
            target_weight=symbol.target_weight,
            signals=[valuation, trend, profit],
            base_amount=amount,
            profile=symbol.strategy_profile,
            weights=symbol.strategy_profile.strategy_weights,
        )
        if result.action == "pause":
            pauses += 1
        results.append(result)
    results, _ = normalize_amounts(results, amount)
    return (
        [(item.symbol, item.amount) for item in results if item.amount > 0],
        reductions,
        pauses,
    )


def run_backtest(args: argparse.Namespace, fee_bps: float) -> dict[str, object]:
    config = load_app_config()
    records = {symbol.id: market_store.load_records(symbol.id) for symbol in config.symbols}
    by_date = {
        symbol: {str(row["date"]): row for row in rows}
        for symbol, rows in records.items()
    }
    dates = market_store.list_trading_dates(start=args.start, end=args.end)
    if len(dates) < 2:
        raise SystemExit("SQLite 行情样本不足，至少需要两个交易日")
    execution = _execution_dates(dates[1:], args.frequency, args.weekday, args.monthday)
    amounts = _period_amounts(dates, execution, args.monthly_cashflow)
    strategy, fixed = Account(), Account()
    values_s: list[float] = []
    values_f: list[float] = []
    contributions: list[float] = []
    output_dates: list[date] = []
    pause_count = 0
    opportunities = 0
    yearly: dict[str, dict[str, float]] = {}
    fee = fee_bps / 10_000

    for index, current_date in enumerate(dates):
        current_rows = {
            symbol.id: by_date[symbol.id].get(current_date)
            for symbol in config.symbols
        }
        current_rows = {key: value for key, value in current_rows.items() if value}
        contribution = amounts.get(current_date, 0.0)
        if contribution:
            strategy.cash += contribution
            fixed.cash += contribution
        if index > 0:
            signal_date = dates[index - 1]
            signal_rows = {
                symbol.id: by_date[symbol.id].get(signal_date)
                for symbol in config.symbols
            }
            signal_rows = {key: value for key, value in signal_rows.items() if value}
            orders, reductions, paused = _strategy_orders(
                config.symbols, signal_rows, strategy, contribution
            )
            if contribution:
                pause_count += paused
                opportunities += len(config.symbols)
            for symbol, ratio, stage in reductions:
                price = _price(current_rows.get(symbol))
                if price:
                    _sell(strategy, symbol, ratio, price, fee)
                    strategy.stages[symbol] = stage
            if contribution:
                for symbol, amount in orders:
                    price = _price(current_rows.get(symbol))
                    if price:
                        _buy(strategy, symbol, amount, price, fee)
        if contribution:
            equal = contribution / len(config.symbols)
            for symbol in config.symbols:
                price = _price(current_rows.get(symbol.id))
                if price:
                    _buy(fixed, symbol.id, equal, price, fee)
            year = current_date[:4]
            yearly.setdefault(year, {"invested": 0.0})
            yearly[year]["invested"] += contribution

        output_dates.append(date.fromisoformat(current_date))
        contributions.append(contribution)
        values_s.append(_value(strategy, current_rows))
        values_f.append(_value(fixed, current_rows))
        year = current_date[:4]
        yearly.setdefault(year, {"invested": 0.0})
        yearly[year]["strategy_end"] = values_s[-1]
        yearly[year]["fixed_end"] = values_f[-1]

    invested = sum(contributions)
    return {
        "strategy": calculate_metrics(
            output_dates, values_s, contributions, invested
        ),
        "fixed": calculate_metrics(output_dates, values_f, contributions, invested),
        "pause_rate": pause_count / opportunities if opportunities else 0.0,
        "yearly": yearly,
        "fees": {"strategy": strategy.fees, "fixed": fixed.fees},
        "sample": (dates[0], dates[-1]),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--monthly-cashflow", type=float, default=10_000)
    parser.add_argument(
        "--frequency", choices=("daily", "weekly", "monthly"), default="monthly"
    )
    parser.add_argument("--weekday", type=int, default=1)
    parser.add_argument("--monthday", type=int, default=1)
    parser.add_argument("--fee-bps", type=float, default=5)
    args = parser.parse_args()

    baseline = run_backtest(args, args.fee_bps)
    sensitivity = {
        bps: run_backtest(args, bps)["strategy"]["ending_value"]
        for bps in (0.0, args.fee_bps, 20.0)
    }
    print(f"样本期：{baseline['sample'][0]} 至 {baseline['sample'][1]}")
    for label, key in (("策略", "strategy"), ("等权固定定投", "fixed")):
        metrics = baseline[key]
        print(
            f"{label}: 累计投入 ¥{metrics['total_invested']:,.2f} | "
            f"期末 ¥{metrics['ending_value']:,.2f} | XIRR {_pct(metrics['xirr'])} | "
            f"TWR {_pct(metrics['twr'])} | 最大回撤 {_pct(metrics['max_drawdown'])} | "
            f"年化波动 {_pct(metrics['volatility'])}"
        )
    print(f"策略暂停率：{baseline['pause_rate']:.2%}")
    print(
        "成本敏感性（策略期末值）：" +
        " / ".join(f"{bps:g}bp=¥{value:,.2f}" for bps, value in sensitivity.items())
    )
    print("年度摘要：")
    for year, summary in baseline["yearly"].items():
        print(
            f"  {year}: 投入 ¥{summary['invested']:,.2f} | "
            f"策略期末 ¥{summary['strategy_end']:,.2f} | "
            f"等权期末 ¥{summary['fixed_end']:,.2f}"
        )
    print(
        "\n注意：仅使用本地 SQLite 的时点数据，信号取 T-1、下一交易日执行；"
        "样本期与 ETF 上市日期限制会造成可比区间差异。估值含代理口径，"
        "历史结果不代表样本外有效性，不构成投资建议。"
    )


if __name__ == "__main__":
    main()
