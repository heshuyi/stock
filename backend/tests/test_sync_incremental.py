"""Incremental sync: skip when fresh; append only missing bars."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.models import SymbolConfig
from app.services import market_store
from app.services.market_data import sync_all, sync_symbol, warehouse_is_fresh


def _sym(symbol_id: str = "HS300") -> SymbolConfig:
    return SymbolConfig(
        id=symbol_id,
        name="测试",
        etf_code="510300",
        index_code="000300",
        akshare_symbol="sh000300",
        target_weight=0.35,
    )


def _seed_bars(symbol: str, n: int, end: date) -> None:
    """Insert n weekday bars ending exactly on `end` (must be a weekday)."""
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    rows = []
    for i, day in enumerate(days):
        close = 4000 + i
        rows.append(
            {
                "date": day.isoformat(),
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1e6,
                "ma_short": close,
                "ma_long": close,
                "source": "akshare",
            }
        )
    market_store.upsert_records(symbol, rows)


def test_warehouse_is_fresh_when_latest_covers_calendar_t1(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None

    # Calendar T-1 = 2026-08-05; warehouse tip matches → fresh.
    monkeypatch.setattr(
        market_store, "expected_trading_t1", lambda today=None: date(2026, 8, 5)
    )
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-08-05",
                "close": 4500,
                "ma_short": 4600,
                "ma_long": 4700,
                "valuation_asof": "2026-08-05",
                "valuation_source": "akshare-legulegu",
                "source": "akshare",
            }
        ],
    )
    assert warehouse_is_fresh("HS300") is not None
    assert warehouse_is_fresh("HS300", force=True) is None


def test_warehouse_not_fresh_when_behind_calendar_t1(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None

    monkeypatch.setattr(
        market_store, "expected_trading_t1", lambda today=None: date(2026, 8, 5)
    )
    # Stale tip: warehouse max would equal resolve_signal_date, but calendar is ahead.
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-07-30",
                "close": 4500,
                "ma_short": 4600,
                "ma_long": 4700,
            }
        ],
    )
    assert warehouse_is_fresh("HS300") is None


def test_sync_symbol_skips_network_when_fresh(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None

    monkeypatch.setattr(
        market_store, "expected_trading_t1", lambda today=None: date(2026, 8, 5)
    )
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-08-05",
                "close": 4500,
                "ma_short": 4600,
                "ma_long": 4700,
                "valuation_asof": "2026-08-05",
                "valuation_source": "akshare-legulegu",
                "source": "akshare",
            }
        ],
    )

    def _boom(*_a, **_k):
        raise AssertionError("should not hit akshare when fresh")

    monkeypatch.setattr("app.services.market_data.fetch_akshare_index", _boom)
    monkeypatch.setattr(
        "app.services.market_data.mirror_latest_to_mongo",
        lambda *_a, **_k: asyncio.sleep(0),
    )

    result = asyncio.run(sync_symbol(_sym(), use_mock=False, force=False))
    assert result["mode"] == "skipped"
    assert result["rows_added"] == 0
    assert result["latest_date"] == "2026-08-05"


def test_price_fresh_stale_valuation_refreshes_instead_of_skipping(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None
    monkeypatch.setattr(
        market_store, "expected_trading_t1", lambda today=None: date(2026, 8, 5)
    )
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-08-05",
                "close": 4500,
                "ma_short": 4600,
                "ma_long": 4700,
                "valuation_asof": "2026-07-20",
                "valuation_source": "akshare-legulegu",
                "source": "akshare",
            }
        ],
    )
    refreshed = {"count": 0}

    def fake_refresh(symbol, ma_short, ma_long):
        refreshed["count"] += 1
        assert symbol.id == "HS300"
        assert ma_short > 0 and ma_long > 0
        return "akshare-legulegu"

    monkeypatch.setattr(
        "app.services.market_data._refresh_valuation_only", fake_refresh
    )
    monkeypatch.setattr(
        "app.services.market_data.mirror_latest_to_mongo",
        lambda *_a, **_k: asyncio.sleep(0),
    )

    result = asyncio.run(sync_symbol(_sym(), use_mock=False, force=False))
    assert refreshed["count"] == 1
    assert result["mode"] == "incremental"
    assert result["mode"] != "skipped"
    assert result["valuation_source"] == "akshare-legulegu"


def test_sync_symbol_incremental_appends_missing_only(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None

    tip = date(2026, 7, 28)
    _seed_bars("HS300", 220, tip)
    monkeypatch.setattr(
        market_store, "expected_trading_t1", lambda today=None: date(2026, 7, 30)
    )

    new_dates = [date(2026, 7, 29), date(2026, 7, 30)]

    def fake_index(symbol, *, start_date=None, with_valuation=True):
        assert start_date == "20260729"
        assert with_valuation is False
        rows = []
        for d in new_dates:
            rows.append(
                {
                    "date": d,
                    "open": 5000,
                    "high": 5010,
                    "low": 4990,
                    "close": 5005,
                    "volume": 1e6,
                    "pe": np.nan,
                    "pb": np.nan,
                }
            )
        return pd.DataFrame(rows), None, None

    monkeypatch.setattr("app.services.market_data.fetch_akshare_index", fake_index)
    monkeypatch.setattr(
        "app.services.market_data.fetch_akshare_etf_history",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.services.market_data.mirror_latest_to_mongo",
        lambda *_a, **_k: asyncio.sleep(0),
    )

    before = market_store.count_bars("HS300")
    result = asyncio.run(sync_symbol(_sym(), use_mock=False, force=False))
    after = market_store.count_bars("HS300")

    assert result["mode"] == "incremental"
    assert result["rows_added"] == 2
    assert after == before + 2
    assert result["latest_date"] == "2026-07-30"


def test_stale_warehouse_does_not_skip_via_circular_t1(monkeypatch, tmp_path):
    """Regression: warehouse tip == resolve_signal_date must NOT skip when calendar ahead."""
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None

    tip = date(2026, 7, 30)
    _seed_bars("HS300", 220, tip)
    monkeypatch.setattr(
        market_store, "expected_trading_t1", lambda today=None: date(2026, 8, 5)
    )

    called = {"n": 0}

    def fake_index(symbol, *, start_date=None, with_valuation=True):
        called["n"] += 1
        assert start_date == "20260731"
        d = date(2026, 8, 5)
        return (
            pd.DataFrame(
                [
                    {
                        "date": d,
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "volume": 1,
                        "pe": np.nan,
                        "pb": np.nan,
                    }
                ]
            ),
            None,
            None,
        )

    monkeypatch.setattr("app.services.market_data.fetch_akshare_index", fake_index)
    monkeypatch.setattr(
        "app.services.market_data.fetch_akshare_etf_history",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.services.market_data.mirror_latest_to_mongo",
        lambda *_a, **_k: asyncio.sleep(0),
    )

    result = asyncio.run(sync_symbol(_sym(), use_mock=False, force=False))
    assert called["n"] >= 1
    assert result["mode"] == "incremental"
    assert result["latest_date"] == "2026-08-05"


def test_sync_all_parallel_and_counts(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None

    async def fake_sync(sym, use_mock=None, force=False):
        return {
            "symbol": sym.id,
            "source": "warehouse",
            "mode": "skipped",
            "rows": 1,
            "rows_added": 0,
            "latest_date": "2026-07-30",
            "latest_close": 1.0,
            "valuation_source": None,
        }

    monkeypatch.setattr("app.services.market_data.sync_symbol", fake_sync)
    monkeypatch.setattr(
        "app.services.market_data.load_app_config",
        lambda: type(
            "C",
            (),
            {
                "symbols": [_sym("HS300"), _sym("ZZ500"), _sym("SZ50")],
                "defaults": type("D", (), {})(),
            },
        )(),
    )
    monkeypatch.setattr(
        market_store, "purge_symbols_not_in", lambda *_a, **_k: []
    )
    monkeypatch.setattr(market_store, "data_status", lambda: {"ok": True})

    out = asyncio.run(sync_all(use_mock=False, force=False, concurrency=3))
    assert out["skipped"] == 3
    assert out["fetched"] == 0
    assert out["incremental"] == 0
    assert out["rows_added"] == 0
    assert len(out["results"]) == 3
