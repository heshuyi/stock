from __future__ import annotations

import math

import pandas as pd

from app.models import SymbolConfig
from app.services import market_store
from app.services.market_data import (
    FULL_VALUATION_WINDOW_YEARS,
    apply_valuation_asof,
    enrich_indicators,
    fetch_akshare_valuations,
)


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


def test_sparse_valuation_percentile_is_missing_not_neutral():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=4).date,
            "close": [10, 11, 12, 13],
            "pe": [10, 11, 12, 13],
            "pb": [1.0, 1.1, 1.2, 1.3],
        }
    )
    result = enrich_indicators(frame, ma_short=2, ma_long=2)
    assert result["pe_percentile"].isna().all()
    assert result["pb_percentile"].isna().all()


def test_five_year_percentile_uses_calendar_year_boundary():
    bars = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-03-01").date()],
            "close": [1.0],
            "pe": [50.0],
            "pb": [5.0],
        }
    )
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2021-03-01",
                    "2022-03-01",
                    "2023-03-01",
                    "2024-03-01",
                    "2026-03-01",
                ]
            ).date,
            "pe": [10, 20, 30, 40, 50],
        }
    )
    result = enrich_indicators(
        bars, ma_short=1, ma_long=1, pe_history=history
    )
    assert result.iloc[-1]["pe_percentile"] == 1.0


def test_sync_does_not_erase_existing_valuation(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)

    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-01-05",
                "close": 4000,
                "pe": 12.3,
                "pb": 1.2,
                "valuation_asof": "2026-01-02",
                "valuation_source": "akshare-legulegu",
            }
        ],
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
    assert row["valuation_asof"] == "2026-01-02"
    assert row["valuation_source"] == "akshare-legulegu"


def test_materialized_sparse_percentile_clears_stale_value(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-01-05",
                "close": 4000,
                "pe": 12.3,
                "pe_percentile": 0.5,
                "valuation_asof": "2026-01-05",
            }
        ],
    )
    market_store.materialize_valuation_metrics(
        "HS300",
        [
            {
                "date": "2026-01-05",
                "pe": 12.3,
                "pb": None,
                "pe_percentile": None,
                "pb_percentile": None,
                "valuation_asof": "2026-01-05",
                "valuation_source": "akshare-legulegu",
            }
        ],
    )
    row = market_store.get_bar("HS300", "2026-01-05")
    assert row is not None
    assert row["pe_percentile"] is None


def test_data_overview_surfaces_split_quality_metrics(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store._READY_PATH = None
    market_store.upsert_records(
        "HS300",
        [
            {
                "date": "2026-01-05",
                "close": 4000,
                "etf_close": 4.0,
                "pe": 12.3,
                "pb": 1.2,
                "valuation_asof": "2026-01-05",
            },
            {
                "date": "2026-01-06",
                "close": None,
                "etf_close": None,
                "pe": None,
                "pb": None,
            },
        ],
    )
    overall = market_store.data_overview()["overall"]
    assert overall["price_completeness_pct"] == 50.0
    assert overall["valuation_completeness_pct"] == 50.0
    assert overall["etf_completeness_pct"] == 50.0
    assert "valuation_freshness_pct" in overall
    assert "quality_score" in overall


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


def test_monthly_proxy_pe_fills_daily_bars_with_asof():
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2023-11-15", "2023-11-20", "2023-11-30", "2023-12-01"]
            ).date,
            "close": [1, 2, 3, 4],
        }
    )
    pe = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-10-31", "2023-11-30"]).date,
            "pe": [40.0, 42.0],
        }
    )
    result = apply_valuation_asof(bars, pe, None)
    assert result["pe"].tolist() == [40.0, 40.0, 42.0, 42.0]
    assert result["pe"].isna().sum() == 0
    assert result["valuation_asof"].astype(str).tolist() == [
        "2023-10-31",
        "2023-10-31",
        "2023-11-30",
        "2023-11-30",
    ]


def test_full_window_percentile_uses_older_history():
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-01", "2026-07-30"]).date,
            "close": [1, 2],
            "pe": [30.0, 30.0],
            "pb": [3.0, 3.0],
        }
    )
    # History older than 5y should still count under full window.
    pe_history = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2018-01-31",
                    "2019-01-31",
                    "2020-01-31",
                    "2022-01-31",
                    "2023-01-31",
                    "2024-01-31",
                    "2025-01-31",
                    "2026-07-30",
                ]
            ).date,
            "pe": [50.0, 45.0, 40.0, 10.0, 15.0, 20.0, 25.0, 30.0],
        }
    )
    five_y = enrich_indicators(
        bars,
        ma_short=2,
        ma_long=2,
        pe_history=pe_history,
        valuation_window_years=5,
    )
    full = enrich_indicators(
        bars,
        ma_short=2,
        ma_long=2,
        pe_history=pe_history,
        valuation_window_years=FULL_VALUATION_WINDOW_YEARS,
    )
    assert five_y.iloc[-1]["pe_percentile"] == 1.0
    assert full.iloc[-1]["pe_percentile"] < five_y.iloc[-1]["pe_percentile"]


def test_proxy_percentile_uses_prelisting_history():
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2023-07-31",
                    "2023-11-30",
                    "2024-11-30",
                    "2025-11-30",
                    "2026-07-30",
                ]
            ).date,
            "close": [1, 2, 3, 4, 5],
            "pe": [55.0, 50.0, 45.0, 42.0, 41.0],
            "pb": [4.2, 4.0, 3.8, 3.5, 3.4],
        }
    )
    # Long monthly history inside the rolling 5y window, mostly below 41.
    pe_history = pd.DataFrame(
        {
            "date": pd.date_range("2021-08-31", periods=55, freq="ME").date,
            "pe": [25 + i * 0.1 for i in range(55)],
        }
    )
    short_only = enrich_indicators(bars, ma_short=2, ma_long=3)
    with_history = enrich_indicators(
        bars, ma_short=2, ma_long=3, pe_history=pe_history, pb_history=None
    )
    # Short window sees 41 as the lowest of 5 points; long history ranks it high.
    assert short_only.iloc[-1]["pe_percentile"] == 0.2
    assert with_history.iloc[-1]["pe_percentile"] > 0.95


def test_purge_removes_retired_cyb_symbol(monkeypatch, tmp_path):
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(market_store, "get_db_path", lambda: db_path)
    market_store.upsert_records(
        "CYB",
        [{"date": "2026-01-05", "close": 3000, "pe": None, "pb": None}],
    )
    market_store.upsert_records(
        "CYB200",
        [{"date": "2026-01-05", "close": 4000, "pe": 41.0, "pb": 4.0}],
    )
    removed = market_store.purge_symbols_not_in({"CYB200", "HS300"})
    assert any(item["symbol"] == "CYB" for item in removed)
    assert market_store.get_bar("CYB", "2026-01-05") is None
    assert market_store.get_bar("CYB200", "2026-01-05") is not None
