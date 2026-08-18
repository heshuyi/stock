"""Manual position reconciliation ledger (R2).

Keeps the tracked portfolio truthful by applying real executed trades
(deposit / buy / sell / dividend) to shares, average cost, cash and
accumulated dividends. Pure and side-effect free so it is easy to test and
safe to call from the API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from app.models import Holding, Portfolio, TradeInput, TradeRecord


class LedgerError(ValueError):
    """A trade cannot be applied as requested (clear user-facing message)."""


def _holding(portfolio: Portfolio, symbol: str) -> Holding:
    for h in portfolio.holdings:
        if h.symbol == symbol:
            return h
    return Holding(symbol=symbol)


def _put_holding(portfolio: Portfolio, holding: Holding) -> None:
    for i, h in enumerate(portfolio.holdings):
        if h.symbol == holding.symbol:
            portfolio.holdings[i] = holding
            return
    portfolio.holdings.append(holding)


def _effective_price(
    trade: TradeInput, price_provider: Callable[[str], float | None]
) -> float | None:
    if trade.price and trade.price > 0:
        return trade.price
    return price_provider(trade.symbol)


def apply_trade(
    portfolio: Portfolio,
    trade: TradeInput,
    price_provider: Callable[[str], float | None],
) -> tuple[Portfolio, TradeRecord]:
    """Apply ``trade`` in place to ``portfolio``; returns (portfolio, record).

    The caller passes the live portfolio object to mutate; the API passes a
    ``model_copy(deep=True)`` so a failed save never corrupts persisted state.
    """
    if trade.kind == "deposit":
        if trade.amount is None or trade.amount <= 0:
            raise LedgerError("入金需要有效的 amount（金额）")
        portfolio.cash += trade.amount
    elif trade.kind == "buy":
        if trade.amount is None or trade.amount <= 0:
            raise LedgerError("买入需要有效的 amount（花费金额）")
        if trade.amount > portfolio.cash + 1e-6:
            raise LedgerError(
                f"可支配储备不足（当前 ¥{portfolio.cash:,.2f}），请先登记入金"
            )
        px = _effective_price(trade, price_provider)
        if not px or px <= 0:
            raise LedgerError("买入缺少有效成交价，请填写 price")
        h = _holding(portfolio, trade.symbol)
        new_shares = trade.amount / px
        total_cost = h.cost_price * h.shares + trade.amount
        h.shares += new_shares
        h.cost_price = total_cost / h.shares if h.shares > 0 else 0.0
        h.market_value = None
        portfolio.cash -= trade.amount
        _put_holding(portfolio, h)
    elif trade.kind == "sell":
        h = _holding(portfolio, trade.symbol)
        shares_out = trade.shares
        if shares_out is None:
            if trade.ratio is None:
                raise LedgerError("卖出需要 shares 或 ratio")
            shares_out = h.shares * trade.ratio
        shares_out = min(shares_out, h.shares)
        if shares_out <= 0:
            raise LedgerError("该标的无持仓可卖")
        px = _effective_price(trade, price_provider)
        if not px or px <= 0:
            raise LedgerError("卖出缺少有效成交价，请填写 price")
        proceeds = shares_out * px
        h.shares -= shares_out
        if h.shares <= 0:
            h.shares = 0.0
            h.cost_price = 0.0
        portfolio.cash += proceeds
        _put_holding(portfolio, h)
    elif trade.kind == "dividend":
        if trade.amount is None or trade.amount <= 0:
            raise LedgerError("分红需要有效的 amount（金额）")
        h = _holding(portfolio, trade.symbol)
        h.dividends_received += trade.amount
        if trade.reinvest:
            px = _effective_price(trade, price_provider)
            if not px or px <= 0:
                raise LedgerError("红利再投缺少有效成交价，请填写 price")
            # reinvested dividends add shares but not cost basis
            h.shares += trade.amount / px
            h.market_value = None
        else:
            portfolio.cash += trade.amount
        _put_holding(portfolio, h)
    else:
        raise LedgerError(f"未知交易类型: {trade.kind}")

    record = TradeRecord(
        **trade.model_dump(),
        applied_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    portfolio.trades.append(record)
    return portfolio, record
