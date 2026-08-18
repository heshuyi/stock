#!/usr/bin/env python3
"""Point-in-time offline comparison of the strategy and equal-weight DCA.

Reuses the live strategy pipeline (``app.services.strategy_pipeline``), so
signal logic, valuation-freshness fail-safes, hard vetoes, profit-taking
locks and the minimum-investment floor behave exactly like the dashboard —
backtest results are no longer optimistic relative to live behaviour.

Supported checks:

* full-sample comparison vs equal-weight fixed DCA (XIRR / TWR / drawdown /
  volatility / pause rate / fee sensitivity / yearly summary)
* out-of-sample split via ``--oos-start`` (metrics for in-sample and OOS
  segments, including carried capital in XIRR)
* growth-bear policy via ``--growth-bear-policy soft|hard_veto`` (mirrors the
  dashboard setting; ``--variant growth-bear-soft`` is a legacy alias)
* one-at-a-time parameter sensitivity scan via ``--sensitivity``

Signals use T-1 closes and execute on the next trading day. Sample is limited
by warehouse history and ETF listing dates; the strategy still needs strict
out-of-sample evidence and historical results do not constitute investment
advice.
"""

from __future__ import annotations

import argparse
import copy
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable

from app.db import load_app_config
from app.models import AppConfig, Holding
from app.services import market_store
from app.services.strategy_pipeline import (
    PipelineInputs,
    apply_growth_bear_policy,
    run_strategy_pipeline,
)
from app.strategies.ensemble import ensure_minimum_investment, normalize_amounts

# Live engine uses these when the cash pool is disabled (pool_factor == 1.0).
_VALUATION_REDUCE_PERCENTILE = 0.80
_VALUATION_EXIT_PERCENTILE = 0.90
_MAX_MULT = 2.0
_CAP_RATIO = 2.0
_GROWTH_IDS = ("CYB200", "KCB50")


@dataclass
class Account:
    cash: float = 0.0
    shares: dict[str, float] = field(default_factory=dict)
    stages: dict[str, int] = field(default_factory=dict)
    armed: dict[str, bool] = field(default_factory=dict)
    peaks: dict[str, float | None] = field(default_factory=dict)
    fees: float = 0.0


def xirr(cashflows: Iterable[tuple[date, float]]) -> float | None:
    """Internal rate of return by bisection with robust bracket search."""
    flows = list(cashflows)
    if not flows or not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    origin = flows[0][0]

    def npv(rate: float) -> float:
        return sum(
            value / ((1 + rate) ** ((when - origin).days / 365.0))
            for when, value in flows
        )

    lo = -0.9999
    f_lo = npv(lo)
    bracketed = False
    hi = 0.05
    for _ in range(64):
        if f_lo * npv(hi) <= 0:
            bracketed = True
            break
        hi *= 2
        if hi > 1e12:
            break
    if not bracketed:
        # Returns between -100% and 0: look for a sign change on the low side.
        hi = -0.5
        for _ in range(64):
            if f_lo * npv(hi) <= 0:
                bracketed = True
                break
            hi = (hi - 1.0) / 2
            if hi <= -0.99995:
                break
    if not bracketed:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


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


def segment_metrics(
    dates: list[date],
    values: list[float],
    contributions: list[float],
    lo: int,
    hi: int | None = None,
) -> dict[str, float | None]:
    """Metrics for dates[lo:hi], carrying the pre-segment value into XIRR.

    ``lo > 0`` treats the value right before the segment as an initial
    outflow so OOS XIRR reflects the return on carried capital too.
    """
    hi = hi or len(dates)
    seg_dates = dates[lo:hi]
    seg_vals = values[lo:hi]
    if not seg_dates:
        return {
            "total_invested": 0.0,
            "starting_value": 0.0,
            "ending_value": 0.0,
            "xirr": None,
            "twr": 0.0,
            "max_drawdown": 0.0,
            "volatility": 0.0,
        }
    start_val = values[lo - 1] if lo > 0 else 0.0
    flows: list[tuple[date, float]] = []
    if lo > 0 and start_val > 0:
        flows.append((seg_dates[0], -start_val))
    for when, amount in zip(seg_dates, contributions[lo:hi]):
        if amount:
            flows.append((when, -amount))
    flows.append((seg_dates[-1], seg_vals[-1]))

    returns: list[float] = []
    for i in range(lo, hi):
        if i == 0:
            continue
        previous, current, contribution = values[i - 1], values[i], contributions[i]
        if previous > 0:
            returns.append((current - contribution) / previous - 1)
    twr = math.prod(1 + value for value in returns) - 1 if returns else 0.0
    volatility = (
        statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    )
    return {
        "total_invested": sum(contributions[lo:hi]),
        "starting_value": start_val,
        "ending_value": seg_vals[-1],
        "xirr": xirr(flows),
        "twr": twr,
        "max_drawdown": max_drawdown(seg_vals),
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


def _account_holdings(
    account: Account, symbols: list
) -> list[Holding]:
    return [
        Holding(
            symbol=s.id,
            shares=account.shares.get(s.id, 0.0),
            cost_price=0.0,
            market_value=None,
            take_profit_stage=account.stages.get(s.id, 0),
            trend_state=None,
            trailing_armed=account.armed.get(s.id, False),
            trail_peak_price=account.peaks.get(s.id),
        )
        for s in symbols
    ]


def _resolve_growth_policy(args: argparse.Namespace) -> str:
    """Resolve the effective growth-bear policy from the CLI.

    ``--variant growth-bear-soft`` is a backward-compatible alias for
    ``--growth-bear-policy soft``; the explicit policy flag wins.
    """
    if getattr(args, "growth_bear_policy", "hard_veto") == "soft":
        return "soft"
    if getattr(args, "variant", "baseline") == "growth-bear-soft":
        return "soft"
    return "hard_veto"


def run_backtest(
    args: argparse.Namespace, fee_bps: float, config: AppConfig | None = None
) -> dict[str, object]:
    config = config or copy.deepcopy(load_app_config())
    # User-facing growth-bear policy (mirrors the live dashboard setting).
    config.symbols = apply_growth_bear_policy(
        config.symbols,
        _resolve_growth_policy(args),
        getattr(args, "growth_bear_mult", 0.2),
    )
    symbol_by_id = {s.id: s for s in config.symbols}
    target_weights = {s.id: s.target_weight for s in config.symbols}

    records = {
        symbol.id: market_store.load_records(symbol.id) for symbol in config.symbols
    }
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
    min_floor = float(getattr(args, "min_floor", 0.0) or 0.0)

    for index, current_date in enumerate(dates):
        current_rows = {
            symbol_id: by_date[symbol_id].get(current_date)
            for symbol_id in symbol_by_id
        }
        current_rows = {key: value for key, value in current_rows.items() if value}
        contribution = amounts.get(current_date, 0.0)
        if contribution:
            strategy.cash += contribution
            fixed.cash += contribution

        # Strategy signals on T-1 close, executed on this trading day
        # (reductions are checked every session, exactly like the live engine).
        if index > 0:
            signal_date = dates[index - 1]
            signal_rows = {
                symbol_id: by_date[symbol_id].get(signal_date)
                for symbol_id in symbol_by_id
            }
            signal_rows = {key: value for key, value in signal_rows.items() if value}
            out = run_strategy_pipeline(
                PipelineInputs(
                    symbols=config.symbols,
                    latest_by_symbol=signal_rows,
                    signal_date=signal_date,
                    base_amount=contribution,
                    target_weights=target_weights,
                    holdings=_account_holdings(strategy, config.symbols),
                    hard_veto_enabled=True,
                    profit_take_enabled=True,
                    valuation_reduce_percentile=_VALUATION_REDUCE_PERCENTILE,
                    valuation_exit_percentile=_VALUATION_EXIT_PERCENTILE,
                    max_mult=_MAX_MULT,
                )
            )
            items = out.items
            if contribution:
                # Mirror live post-processing order: floor → cap normalization.
                if min_floor > 0 and not out.valuation_issues:
                    items, _ = ensure_minimum_investment(
                        items, contribution, min_floor
                    )
                items, _ = normalize_amounts(items, contribution, _CAP_RATIO)

            # Persist pipeline-derived strategy state into the account.
            for h in out.updated_holdings:
                strategy.stages[h.symbol] = h.take_profit_stage
                strategy.armed[h.symbol] = h.trailing_armed
                strategy.peaks[h.symbol] = h.trail_peak_price

            for item in items:
                if item.reduce_ratio:
                    price = _price(current_rows.get(item.symbol))
                    if price:
                        _sell(strategy, item.symbol, item.reduce_ratio, price, fee)
            if contribution:
                for item in items:
                    if item.action == "buy" and item.amount > 0:
                        price = _price(current_rows.get(item.symbol))
                        if price:
                            _buy(strategy, item.symbol, item.amount, price, fee)
                pause_count += sum(1 for item in items if item.action == "pause")
                pause_count += len(out.missing_symbols)  # pre-listing = no signal
                opportunities += len(config.symbols)

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
        "dates": output_dates,
        "values": values_s,
        "values_fixed": values_f,
        "contributions": contributions,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _print_metrics(label: str, metrics: dict[str, float | None]) -> None:
    print(
        f"{label}: 累计投入 ¥{metrics['total_invested']:,.2f} | "
        f"期末 ¥{metrics['ending_value']:,.2f} | XIRR {_pct(metrics['xirr'])} | "
        f"TWR {_pct(metrics['twr'])} | 最大回撤 {_pct(metrics['max_drawdown'])} | "
        f"年化波动 {_pct(metrics['volatility'])}"
    )


def _print_oos_report(result: dict[str, object], oos_start: str) -> None:
    dates = result["dates"]
    lo = next((i for i, d in enumerate(dates) if d.isoformat() >= oos_start), len(dates))
    if lo == 0:
        print(f"\n样本外起点 {oos_start} 早于样本起点，无法分割")
        return
    print(f"\n样本外（>= {oos_start}）:")
    _print_metrics(
        "  策略",
        segment_metrics(dates, result["values"], result["contributions"], lo),
    )
    _print_metrics(
        "  等权固定定投",
        segment_metrics(dates, result["values_fixed"], result["contributions"], lo),
    )
    print("样本内（< " + oos_start + "）:")
    _print_metrics(
        "  策略",
        segment_metrics(dates, result["values"], result["contributions"], 0, lo),
    )
    _print_metrics(
        "  等权固定定投",
        segment_metrics(
            dates, result["values_fixed"], result["contributions"], 0, lo
        ),
    )


def _sensitivity_rows(args: argparse.Namespace, fee_bps: float) -> list[tuple[str, dict[str, object]]]:
    """One-at-a-time parameter scan; every other knob stays at baseline."""
    def run(
        name: str, mutate: Callable[[AppConfig], None], variant: str = "baseline"
    ) -> tuple[str, dict[str, object]]:
        cfg = copy.deepcopy(load_app_config())
        mutate(cfg)
        scan_args = copy.deepcopy(args)
        scan_args.variant = variant
        return name, run_backtest(scan_args, fee_bps, config=cfg)

    rows = [run("baseline", lambda c: None)]

    def scale_pause(factor: float) -> Callable[[AppConfig], None]:
        def mutate(cfg: AppConfig) -> None:
            for s in cfg.symbols:
                p = s.strategy_profile
                s.strategy_profile = p.model_copy(
                    update={"pause_percentile": min(1.0, p.pause_percentile * factor)}
                )
        return mutate

    def set_val_weight(wv: float) -> Callable[[AppConfig], None]:
        def mutate(cfg: AppConfig) -> None:
            for s in cfg.symbols:
                p = s.strategy_profile
                s.strategy_profile = p.model_copy(
                    update={"strategy_weights": {"valuation": wv, "trend": 1.0 - wv}}
                )
        return mutate

    def scale_tiers(factor: float) -> Callable[[AppConfig], None]:
        def mutate(cfg: AppConfig) -> None:
            for s in cfg.symbols:
                p = s.strategy_profile
                s.strategy_profile = p.model_copy(
                    update={"tier_mults": [m * factor for m in p.tier_mults]}
                )
        return mutate

    def set_trail_dd(dd: float) -> Callable[[AppConfig], None]:
        def mutate(cfg: AppConfig) -> None:
            for s in cfg.symbols:
                p = s.strategy_profile
                s.strategy_profile = p.model_copy(update={"trail_drawdown": dd})
        return mutate

    def growth_bear_soft() -> Callable[[AppConfig], None]:
        def mutate(cfg: AppConfig) -> None:
            for s in cfg.symbols:
                if s.id in _GROWTH_IDS:
                    p = s.strategy_profile
                    s.strategy_profile = p.model_copy(update={"bear_soft_mult": 0.2})
        return mutate

    rows.append(run("估值暂停分位 ×0.85", scale_pause(0.85)))
    rows.append(run("估值暂停分位 ×1.15", scale_pause(1.15)))
    rows.append(run("估值权重 0.5 / 趋势 0.5", set_val_weight(0.5)))
    rows.append(run("估值权重 0.9 / 趋势 0.1", set_val_weight(0.9)))
    rows.append(run("倍数档 ×0.85", scale_tiers(0.85)))
    rows.append(run("倍数档 ×1.15", scale_tiers(1.15)))
    rows.append(run("追踪回撤 6%", set_trail_dd(0.06)))
    rows.append(run("追踪回撤 14%", set_trail_dd(0.14)))
    rows.append(
        run("成长仓软降频 0.2×", growth_bear_soft(), variant="growth-bear-soft")
    )
    return rows


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
    parser.add_argument(
        "--oos-start",
        help="样本外分割起点（含该日）；输出样本内/样本外分段指标",
    )
    parser.add_argument(
        "--variant",
        choices=("baseline", "growth-bear-soft"),
        default="baseline",
        help="策略变体（研究用）：growth-bear-soft 等价于 --growth-bear-policy soft",
    )
    parser.add_argument(
        "--growth-bear-policy",
        choices=("hard_veto", "soft"),
        default="hard_veto",
        help="成长仓空头策略：hard_veto=防守（硬停，默认） / soft=追收益（软降频，与设置页一致）",
    )
    parser.add_argument(
        "--growth-bear-mult",
        type=float,
        default=0.2,
        help="soft 模式下成长仓空头排列的买入倍数（0–1）",
    )
    parser.add_argument(
        "--min-floor",
        type=float,
        default=0.0,
        help="全组合暂停时保留的底仓比例（0 表示关闭，与实盘默认一致）",
    )
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help="运行单因子敏感性扫描（其余参数保持基线）",
    )
    args = parser.parse_args()

    if args.sensitivity:
        print("单因子敏感性扫描（每次仅调整一个旋钮，其余保持基线）：")
        print(f"{'变体':<22} | {'XIRR':>8} | {'TWR':>8} | {'最大回撤':>8} | {'暂停率':>8}")
        for name, result in _sensitivity_rows(args, args.fee_bps):
            m = result["strategy"]
            print(
                f"{name:<22} | {_pct(m['xirr']):>8} | {_pct(m['twr']):>8} | "
                f"{_pct(m['max_drawdown']):>8} | {_pct(result['pause_rate']):>8}"
            )
        print(
            "\n注：敏感性只反映单旋钮方向与幅度，不构成调参建议；"
            "任何参数选择都需要样本外证据支撑。"
        )
        return

    baseline = run_backtest(args, args.fee_bps)
    sensitivity = {
        bps: run_backtest(args, bps)["strategy"]["ending_value"]
        for bps in (0.0, args.fee_bps, 20.0)
    }
    print(f"样本期：{baseline['sample'][0]} 至 {baseline['sample'][1]}")
    for label, key in (("策略", "strategy"), ("等权固定定投", "fixed")):
        _print_metrics(label, baseline[key])
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
    if args.oos_start:
        _print_oos_report(baseline, args.oos_start)
    policy = _resolve_growth_policy(args)
    if policy == "soft":
        print(f"\n成长仓空头策略：soft（软降频 {args.growth_bear_mult:.2f}×）")
    print(
        "\n注意：回测复用实盘策略管线（含估值新鲜度 fail-safe 与止盈锁），"
        "信号取 T-1、下一交易日执行；样本期与 ETF 上市日期限制会造成可比区间差异。"
        "估值含代理口径，历史结果不代表样本外有效性，不构成投资建议。"
    )


if __name__ == "__main__":
    main()
