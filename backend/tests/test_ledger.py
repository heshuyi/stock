"""Tests for the manual position-reconciliation ledger (R2)."""

from __future__ import annotations

import pytest

from app.models import Holding, Portfolio, TradeInput
from app.services.ledger import LedgerError, apply_trade


def _portfolio(cash: float = 10000.0) -> Portfolio:
    return Portfolio(
        cash=cash,
        holdings=[Holding(symbol="HS300", shares=100, cost_price=4.0)],
    )


def test_deposit_adds_cash():
    p = _portfolio()
    p, rec = apply_trade(
        p, TradeInput(symbol="HS300", kind="deposit", amount=5000), lambda s: None
    )
    assert p.cash == 15000
    assert rec.kind == "deposit"
    assert rec.applied_at
    assert len(p.trades) == 1


def test_buy_updates_weighted_cost():
    p = _portfolio()
    p, _ = apply_trade(
        p, TradeInput(symbol="HS300", kind="buy", amount=400, price=4.0), lambda s: None
    )
    h = p.holdings[0]
    assert h.shares == 200
    assert h.cost_price == 4.0
    assert p.cash == 9600


def test_buy_exceeding_cash_rejected():
    p = _portfolio(cash=100)
    with pytest.raises(LedgerError, match="可支配储备不足"):
        apply_trade(
            p,
            TradeInput(symbol="HS300", kind="buy", amount=500, price=4.0),
            lambda s: None,
        )


def test_dividend_cash_vs_reinvest():
    p = _portfolio()
    p, _ = apply_trade(
        p, TradeInput(symbol="HS300", kind="dividend", amount=30), lambda s: None
    )
    assert p.cash == 10030
    assert p.holdings[0].dividends_received == 30

    p, _ = apply_trade(
        p,
        TradeInput(symbol="HS300", kind="dividend", amount=20, reinvest=True, price=4.0),
        lambda s: None,
    )
    # reinvested dividend adds shares but not cost basis
    assert p.holdings[0].shares == 105
    assert p.holdings[0].cost_price == 4.0
    assert p.holdings[0].dividends_received == 50
    assert p.cash == 10030  # cash unchanged by reinvest


def test_sell_by_ratio_and_shares():
    p = _portfolio()
    p, _ = apply_trade(
        p, TradeInput(symbol="HS300", kind="sell", ratio=0.5, price=4.0), lambda s: None
    )
    assert p.holdings[0].shares == 50
    assert p.cash == 10200

    p, _ = apply_trade(
        p, TradeInput(symbol="HS300", kind="sell", shares=25, price=4.0), lambda s: None
    )
    assert p.holdings[0].shares == 25


def test_sell_without_position_rejected():
    p = _portfolio()
    p.holdings[0].shares = 0
    with pytest.raises(LedgerError, match="无持仓"):
        apply_trade(
            p, TradeInput(symbol="HS300", kind="sell", shares=10, price=4.0), lambda s: None
        )


def test_missing_price_uses_provider():
    p = _portfolio(cash=1000)
    p, _ = apply_trade(
        p,
        TradeInput(symbol="HS300", kind="buy", amount=400),
        lambda s: 2.0,
    )
    assert p.holdings[0].shares == 300  # 100 + 400/2.0
    assert p.cash == 600


def test_trades_do_not_mutate_input_reference():
    original = _portfolio()
    clone = original.model_copy(deep=True)
    apply_trade(clone, TradeInput(symbol="HS300", kind="deposit", amount=1), lambda s: None)
    assert original.cash == 10000
    assert len(original.trades) == 0
