from __future__ import annotations

import math

import pandas as pd

from app.models import SymbolConfig
from app.services import market_store
from app.services.market_data import enrich_indicators, fetch_akshare_valuations


def test_missing_valuation_is_not_backfilled_from_future():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=6).date,
            "close": [10, 11, 12, 13, 14, 15],
            "pe": [None, 10, 11, 12, 13, 14],
            "pb": [None, 1, 1.1, 1.2, 1.3, 1.4],
        }
    )
    result = enrich_indicators(frame, ma_short=3, ma_long=5)
    assert math.isnan(result.iloc[0]["pe_percentile"])
    assert result.iloc[-1]["pe_percentile"] == 1.0


def test_sync_does_not_erase_existing_valuation(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)

    market_store.upsert_records(
        "HS300",
        [{"date": "2026-01-05", "close": 4000, "pe": 12.3, "pb": 1.2}],
    )
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-01-05",
                "close": 4010,
                "pe": None,
                "pb": None,
                "etf_close": 4.1,
            }
        ],
    )

    row = market_store.get_bar("HS300", "2026-01-05")
    assert row is not None
    assert row["close"] == 4010
    assert row["pe"] == 12.3
    assert row["pb"] == 1.2
    assert row["etf_close"] == 4.1


def test_chinext_200_uses_explicit_market_valuation_proxy():
    class FakeAkshare:
        @staticmethod
        def stock_market_pe_lg(symbol):
            assert symbol == "创业板"
            return pd.DataFrame(
                {"日期": ["2026-06-30", "2026-07-30"], "平均市盈率": [39.2, 41.5]}
            )

        @staticmethod
        def stock_market_pb_lg(symbol):
            assert symbol == "创业板"
            return pd.DataFrame(
                {"日期": ["2026-07-29", "2026-07-30"], "市净率": [4.2, 4.1]}
            )

        @staticmethod
        def stock_index_pe_lg(symbol):
            raise AssertionError("proxy must not call index PE endpoint")

        @staticmethod
        def stock_index_pb_lg(symbol):
            raise AssertionError("proxy must not call index PB endpoint")

    symbol = SymbolConfig(
        id="CYB200",
        name="创业板200",
        etf_code="159572",
        index_code="399019",
        akshare_symbol="sz399019",
        pe_symbol="创业板",
        pb_symbol="创业板",
        valuation_enabled=True,
        valuation_proxy=True,
        target_weight=0.15,
    )

    pe, pb = fetch_akshare_valuations(FakeAkshare(), symbol)

    assert pe is not None and pe["pe"].tolist() == [39.2, 41.5]
    assert pb is not None and pb["pb"].tolist() == [4.2, 4.1]


def test_star_50_market_proxy_accepts_star_market_pe_schema():
    class FakeAkshare:
        @staticmethod
        def stock_market_pe_lg(symbol):
            assert symbol == "科创版"
            return pd.DataFrame(
                {"日期": ["2026-07-29", "2026-07-30"], "市盈率": [108.0, 110.1]}
            )

        @staticmethod
        def stock_market_pb_lg(symbol):
            assert symbol == "科创版"
            return pd.DataFrame(
                {"日期": ["2026-07-29", "2026-07-30"], "市净率": [7.9, 8.0]}
            )

    symbol = SymbolConfig(
        id="KCB50",
        name="科创50",
        etf_code="588000",
        index_code="000688",
        akshare_symbol="sh000688",
        pe_symbol="科创版",
        pb_symbol="科创版",
        valuation_enabled=True,
        valuation_proxy=True,
        valuation_proxy_label="科创板市场代理估值",
        target_weight=0.10,
    )

    pe, pb = fetch_akshare_valuations(FakeAkshare(), symbol)

    assert pe is not None and pe["pe"].tolist() == [108.0, 110.1]
    assert pb is not None and pb["pb"].tolist() == [7.9, 8.0]
