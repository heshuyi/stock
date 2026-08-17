from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.config import get_settings
from app.db import get_db, load_app_config
from app.models import SymbolConfig
from app.services import market_store
from app.services.schedule import xshg_sessions

VALUATION_WINDOW_YEARS = 5
FULL_VALUATION_WINDOW_YEARS = 100
MAX_VALUATION_LAG_SESSIONS = 5
FREE_VALUATION_SYMBOLS = {"HS300", "ZZ500", "CYB200", "KCB50", "SZ50"}
# Default 1: parallel akshare + py_mini_racer can native-abort the process.
SYNC_CONCURRENCY = int(os.environ.get("STOCK_SYNC_CONCURRENCY", "1"))
# Bars kept from warehouse when appending new sessions (MA120 + 1y drawdown).
INCREMENTAL_LOOKBACK_BARS = 320

logger = logging.getLogger(__name__)


def warehouse_is_fresh(symbol_id: str, *, force: bool = False) -> dict[str, Any] | None:
    """If warehouse already covers expected calendar T-1, return skip context.

    Compare against expected_trading_t1 (weekday), NOT warehouse max(date) —
    otherwise a stale tip always looks "fresh" and sync never catches up.
    """
    if force:
        return None
    expected = market_store.expected_trading_t1().isoformat()
    latest = market_store.get_latest_bar(symbol_id)
    if not latest:
        return None
    if str(latest.get("date") or "") < expected:
        return None
    if latest.get("close") is None or latest.get("ma_long") is None:
        return None
    return {
        "signal_date": expected,
        "latest": latest,
        "rows": market_store.count_bars(symbol_id),
    }


def valuation_window_years_for(symbol: SymbolConfig) -> int:
    window = getattr(symbol.strategy_profile, "percentile_window", "5y")
    if window == "full":
        return FULL_VALUATION_WINDOW_YEARS
    return VALUATION_WINDOW_YEARS


def valuation_lag_sessions(
    valuation_asof: str | date | None,
    signal_date: str | date | None,
) -> int | None:
    """Count XSHG sessions strictly after valuation_asof through signal_date."""
    if not valuation_asof or not signal_date:
        return None
    observed = (
        valuation_asof
        if isinstance(valuation_asof, date)
        else date.fromisoformat(str(valuation_asof)[:10])
    )
    signal = (
        signal_date
        if isinstance(signal_date, date)
        else date.fromisoformat(str(signal_date)[:10])
    )
    if observed >= signal:
        return 0
    return len(xshg_sessions(observed + timedelta(days=1), signal))


def generate_mock_history(symbol: SymbolConfig, days: int = 800) -> pd.DataFrame:
    """Synthetic OHLCV + PE/PB for offline / fallback use."""
    rng = np.random.default_rng(abs(hash(symbol.id)) % (2**32))
    end = date.today()
    dates = pd.bdate_range(end=end, periods=days)
    drift = {
        "HS300": 0.00015,
        "ZZ500": 0.0001,
        "CYB200": 0.00005,
        "KCB50": 0.00004,
        "SZ50": 0.00012,
    }.get(symbol.id, 0.0001)
    vol = {
        "HS300": 0.012,
        "ZZ500": 0.015,
        "CYB200": 0.02,
        "KCB50": 0.022,
        "SZ50": 0.011,
    }.get(symbol.id, 0.014)
    base = {
        "HS300": 3800,
        "ZZ500": 5500,
        "CYB200": 2100,
        "KCB50": 1000,
        "SZ50": 2600,
    }.get(symbol.id, 3000)
    rets = rng.normal(drift, vol, size=len(dates))
    mid = len(dates) // 2
    rets[mid : mid + 40] -= 0.004
    prices = base * np.cumprod(1 + rets)
    pe_base = {
        "HS300": 12,
        "ZZ500": 22,
        "CYB200": 35,
        "KCB50": 55,
        "SZ50": 10,
    }.get(symbol.id, 15)
    pb_base = {
        "HS300": 1.4,
        "ZZ500": 1.8,
        "CYB200": 3.5,
        "KCB50": 5,
        "SZ50": 1.1,
    }.get(symbol.id, 1.5)
    pe = pe_base * (prices / prices.mean()) * (0.9 + 0.2 * rng.random(len(dates)))
    pb = pb_base * (prices / prices.mean()) * (0.9 + 0.2 * rng.random(len(dates)))

    return pd.DataFrame(
        {
            "date": dates.date,
            "open": prices * (1 + rng.normal(0, 0.002, len(dates))),
            "high": prices * (1 + np.abs(rng.normal(0.005, 0.003, len(dates)))),
            "low": prices * (1 - np.abs(rng.normal(0.005, 0.003, len(dates)))),
            "close": prices,
            "volume": rng.integers(1e7, 5e7, len(dates)),
            "pe": pe,
            "pb": pb,
        }
    )


def load_legacy_free_valuations(symbol: SymbolConfig) -> pd.DataFrame | None:
    """Bootstrap verified free LG observations saved by earlier successful syncs."""
    if symbol.id not in FREE_VALUATION_SYMBOLS:
        return None
    path = Path(__file__).resolve().parents[2] / "data" / "cache" / f"{symbol.id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = [
            {
                "date": pd.to_datetime(row["date"]).date(),
                "pe": row.get("pe"),
                "pb": row.get("pb"),
            }
            for row in payload.get("records", [])
            if row.get("pe") is not None or row.get("pb") is not None
        ]
        frame = pd.DataFrame(rows)
        return frame.drop_duplicates("date").sort_values("date") if not frame.empty else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def fetch_akshare_etf_history(
    symbol: SymbolConfig,
    *,
    start_date: str | None = None,
) -> pd.DataFrame | None:
    """Fetch unadjusted ETF closes; start_date=YYYYMMDD for incremental range."""
    start = start_date or "19900101"
    end = date.today().strftime("%Y%m%d")
    frame = None
    try:
        import akshare as ak

        frame = ak.fund_etf_hist_em(
            symbol=symbol.etf_code,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="",
        )
    except Exception:
        frame = None

    if frame is not None and not frame.empty:
        frame = frame.rename(columns={"日期": "date", "收盘": "etf_close"})
    else:
        try:
            import akshare as ak

            exchange = "sh" if symbol.etf_code.startswith(("5", "6")) else "sz"
            frame = ak.fund_etf_hist_sina(symbol=f"{exchange}{symbol.etf_code}")
            if frame is not None:
                frame = frame.rename(columns={"close": "etf_close"})
        except Exception:
            return None

    if frame is None or frame.empty or not {"date", "etf_close"}.issubset(frame.columns):
        return None
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["etf_close"] = pd.to_numeric(frame["etf_close"], errors="coerce")
    out = frame[["date", "etf_close"]].dropna().sort_values("date")
    if start_date:
        cut = datetime.strptime(start_date, "%Y%m%d").date()
        out = out[out["date"] >= cut]
    return out


def fetch_akshare_valuations(
    ak: Any, symbol: SymbolConfig
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Fetch index valuation or an explicitly configured market proxy."""
    pe_df = pb_df = None
    if symbol.pe_symbol:
        try:
            if symbol.valuation_proxy:
                pe_raw = ak.stock_market_pe_lg(symbol=symbol.pe_symbol)
                pe_column = (
                    "平均市盈率"
                    if "平均市盈率" in pe_raw.columns
                    else "市盈率"
                )
            else:
                pe_raw = ak.stock_index_pe_lg(symbol=symbol.pe_symbol)
                pe_column = "滚动市盈率"
            if pe_raw is not None and not pe_raw.empty:
                pe_df = pe_raw.rename(columns={"日期": "date", pe_column: "pe"})
                pe_df["date"] = pd.to_datetime(pe_df["date"]).dt.date
                pe_df = pe_df[["date", "pe"]].dropna()
        except Exception:
            pe_df = None

    if symbol.pb_symbol:
        try:
            if symbol.valuation_proxy:
                pb_raw = ak.stock_market_pb_lg(symbol=symbol.pb_symbol)
            else:
                pb_raw = ak.stock_index_pb_lg(symbol=symbol.pb_symbol)
            if pb_raw is not None and not pb_raw.empty:
                pb_df = pb_raw.rename(columns={"日期": "date", "市净率": "pb"})
                pb_df["date"] = pd.to_datetime(pb_df["date"]).dt.date
                pb_df = pb_df[["date", "pb"]].dropna()
        except Exception:
            pb_df = None

    return pe_df, pb_df


def apply_valuation_asof(
    df: pd.DataFrame,
    pe_df: pd.DataFrame | None,
    pb_df: pd.DataFrame | None,
    valuation_source: str | None = None,
) -> pd.DataFrame:
    """Attach PE/PB and the matched observation date by backward as-of fill."""
    out = df.sort_values("date").reset_index(drop=True).copy()
    out["_dt"] = pd.to_datetime(out["date"])

    def _asof_col(
        source: pd.DataFrame | None, col: str
    ) -> tuple[pd.Series, pd.Series]:
        if source is None or source.empty or col not in source.columns:
            return (
                pd.Series(np.nan, index=out.index),
                pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]"),
            )
        hist = source.dropna(subset=[col]).sort_values("date").copy()
        if hist.empty:
            return (
                pd.Series(np.nan, index=out.index),
                pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]"),
            )
        hist["_valuation_dt"] = pd.to_datetime(hist["date"])
        merged = pd.merge_asof(
            out[["_dt"]],
            hist[["_valuation_dt", col]],
            left_on="_dt",
            right_on="_valuation_dt",
            direction="backward",
        )
        return merged[col], merged["_valuation_dt"]

    out["pe"], pe_asof = _asof_col(pe_df, "pe")
    out["pb"], pb_asof = _asof_col(pb_df, "pb")
    out["valuation_asof"] = pd.concat([pe_asof, pb_asof], axis=1).min(axis=1)
    out["valuation_asof"] = out["valuation_asof"].dt.date
    out["valuation_source"] = valuation_source
    return out.drop(columns=["_dt"])


def _percentile_against_history(
    dates: list[Any],
    values: np.ndarray,
    history: pd.DataFrame | None,
    value_col: str,
    window_years: int = VALUATION_WINDOW_YEARS,
) -> list[float]:
    """Rank each value against the provider history ending on that date."""
    if history is None or history.empty or value_col not in history.columns:
        return [float("nan")] * len(values)

    hist = history.dropna(subset=[value_col]).sort_values("date").copy()
    if hist.empty:
        return [float("nan")] * len(values)

    hist_dates = pd.to_datetime(hist["date"]).to_numpy(dtype="datetime64[ns]")
    hist_vals = hist[value_col].to_numpy(dtype=float)

    # Precompute inclusive [start, end] window bounds for every bar at once
    # (searchsorted instead of rebuilding a full-history boolean mask per date).
    end_ts = pd.to_datetime(dates)
    ends = end_ts.to_numpy(dtype="datetime64[ns]")
    starts = (end_ts - pd.DateOffset(years=window_years)).to_numpy(
        dtype="datetime64[ns]"
    )
    lo = np.searchsorted(hist_dates, starts, side="left")
    hi = np.searchsorted(hist_dates, ends, side="right")

    out: list[float] = []
    for i, value in enumerate(values):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            out.append(float("nan"))
            continue
        window = int(hi[i]) - int(lo[i])
        if window < 5:
            out.append(float("nan"))
        else:
            out.append(
                float((hist_vals[int(lo[i]) : int(hi[i])] <= float(value)).mean())
            )
    return out


def _normalize_index_frame(df: pd.DataFrame) -> pd.DataFrame | None:
    colmap = {
        "date": "date",
        "日期": "date",
        "open": "open",
        "开盘": "open",
        "high": "high",
        "最高": "high",
        "low": "low",
        "最低": "low",
        "close": "close",
        "收盘": "close",
        "volume": "volume",
        "成交量": "volume",
    }
    rename = {c: colmap[c] for c in df.columns if c in colmap}
    df = df.rename(columns=rename)
    needed = ["date", "open", "high", "low", "close"]
    if not all(c in df.columns for c in needed):
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.date
    if "volume" not in df.columns:
        df["volume"] = 0
    return df.sort_values("date").reset_index(drop=True)


def fetch_akshare_index(
    symbol: SymbolConfig,
    *,
    start_date: str | None = None,
    with_valuation: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None] | None:
    """Fetch index OHLCV (+ optional PE/PB). start_date=YYYYMMDD for incremental.

    Prefer ranged `index_zh_a_hist` when start_date is set so we do not pull
    the entire history on every sync.
    """
    try:
        import akshare as ak

        end = date.today().strftime("%Y%m%d")
        df = None
        if start_date:
            try:
                df = ak.index_zh_a_hist(
                    symbol=symbol.index_code,
                    period="daily",
                    start_date=start_date,
                    end_date=end,
                )
            except Exception:
                df = None
        if df is None or df.empty:
            df = ak.stock_zh_index_daily(symbol=symbol.akshare_symbol)
            if df is None or df.empty:
                df = ak.index_zh_a_hist(
                    symbol=symbol.index_code,
                    period="daily",
                    start_date=start_date or "19910101",
                    end_date=end,
                )
        if df is None or df.empty:
            return None

        df = _normalize_index_frame(df)
        if df is None or df.empty:
            return None
        if start_date:
            cut = datetime.strptime(start_date, "%Y%m%d").date()
            df = df[df["date"] >= cut].reset_index(drop=True)
            if df.empty:
                return (
                    df,
                    None,
                    None,
                )

        pe_df = pb_df = None
        if with_valuation:
            pe_df, pb_df = fetch_akshare_valuations(ak, symbol)
            df = apply_valuation_asof(df, pe_df, pb_df)
        else:
            df["pe"] = np.nan
            df["pb"] = np.nan
            df["valuation_asof"] = None
            df["valuation_source"] = None
        return (
            df[
                [
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "pe",
                    "pb",
                    "valuation_asof",
                    "valuation_source",
                ]
            ],
            pe_df,
            pb_df,
        )
    except Exception:
        logger.exception("fetch_akshare_index failed for %s", symbol.id)
        return None


def enrich_indicators(
    df: pd.DataFrame,
    ma_short: int = 60,
    ma_long: int = 120,
    pe_history: pd.DataFrame | None = None,
    pb_history: pd.DataFrame | None = None,
    valuation_window_years: int = VALUATION_WINDOW_YEARS,
) -> pd.DataFrame:
    out = df.copy()
    out["ma_short"] = out["close"].rolling(
        ma_short, min_periods=min(ma_short, max(5, ma_short // 3))
    ).mean()
    out["ma_long"] = out["close"].rolling(
        ma_long, min_periods=min(ma_long, max(10, ma_long // 3))
    ).mean()
    rolling_high = out["close"].rolling(252, min_periods=20).max()
    out["high_1y"] = rolling_high
    out["drawdown"] = (rolling_high - out["close"]) / rolling_high

    dates = out["date"].tolist()
    if pe_history is not None and not pe_history.empty:
        out["pe_percentile"] = _percentile_against_history(
            dates,
            out["pe"].to_numpy(dtype=float),
            pe_history,
            "pe",
            window_years=valuation_window_years,
        )
    else:
        out["pe_percentile"] = _percentile_against_history(
            dates,
            out["pe"].to_numpy(dtype=float),
            out[["date", "pe"]],
            "pe",
            window_years=valuation_window_years,
        )

    if pb_history is not None and not pb_history.empty:
        out["pb_percentile"] = _percentile_against_history(
            dates,
            out["pb"].to_numpy(dtype=float),
            pb_history,
            "pb",
            window_years=valuation_window_years,
        )
    else:
        out["pb_percentile"] = _percentile_against_history(
            dates,
            out["pb"].to_numpy(dtype=float),
            out[["date", "pb"]],
            "pb",
            window_years=valuation_window_years,
        )
    return out


def _f(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_str(v: Any) -> str | None:
    if v is None or pd.isna(v):
        return None
    return pd.Timestamp(v).date().isoformat()


def df_to_records(symbol_id: str, df: pd.DataFrame, source: str) -> list[dict[str, Any]]:
    records = []
    for _, row in df.iterrows():
        d = row["date"]
        if isinstance(d, datetime):
            d = d.date()
        date_str = d.isoformat() if hasattr(d, "isoformat") else str(d)
        records.append(
            {
                "symbol": symbol_id,
                "date": date_str,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
                "etf_close": _f(row.get("etf_close")),
                "ma_short": _f(row.get("ma_short")),
                "ma_long": _f(row.get("ma_long")),
                "high_1y": _f(row.get("high_1y")),
                "drawdown": _f(row.get("drawdown")),
                "pe": _f(row.get("pe")),
                "pb": _f(row.get("pb")),
                "pe_percentile": _f(row.get("pe_percentile")),
                "pb_percentile": _f(row.get("pb_percentile")),
                "valuation_asof": _date_str(row.get("valuation_asof")),
                "valuation_source": row.get("valuation_source"),
                "source": source,
            }
        )
    return records


async def mirror_latest_to_mongo(symbol_id: str, record: dict[str, Any]) -> None:
    """Keep a thin latest snapshot in Mongo for API compatibility."""
    db = get_db()
    await db.market_daily.update_one(
        {"symbol": symbol_id, "date": record["date"]},
        {
            "$set": {
                k: record.get(k)
                for k in (
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "ma_short",
                    "ma_long",
                    "high_1y",
                    "drawdown",
                    "valuation_asof",
                    "valuation_source",
                    "source",
                )
            }
        },
        upsert=True,
    )
    await db.valuations.update_one(
        {"symbol": symbol_id, "date": record["date"]},
        {
            "$set": {
                "symbol": symbol_id,
                "date": record["date"],
                "pe": record.get("pe"),
                "pb": record.get("pb"),
                "pe_percentile": record.get("pe_percentile"),
                "pb_percentile": record.get("pb_percentile"),
                "valuation_asof": record.get("valuation_asof"),
                "valuation_source": record.get("valuation_source"),
                "source": record.get("valuation_source") or record.get("source"),
            }
        },
        upsert=True,
    )


async def hydrate_db_from_disk_cache() -> int:
    """Warm warehouse: migrate legacy JSON if needed, mirror latest bars only."""
    return await warm_latest_snapshots(migrate_legacy=True)


async def warm_latest_snapshots(migrate_legacy: bool = False) -> int:
    """Lightweight warm — only latest bar per symbol (no full-history load)."""
    market_store.ensure_store()
    cfg = load_app_config()
    loaded = 0
    for sym in cfg.symbols:
        latest = market_store.get_latest_bar(sym.id)
        if latest:
            await mirror_latest_to_mongo(sym.id, latest)
            loaded += 1
            continue
        if not migrate_legacy:
            continue
        from pathlib import Path
        import json

        legacy = Path(__file__).resolve().parents[2] / "data" / "cache" / f"{sym.id}.json"
        if not legacy.exists():
            continue
        try:
            payload = json.loads(legacy.read_text(encoding="utf-8"))
            records = payload.get("records") or []
            if records:
                market_store.upsert_records(sym.id, records)
                market_store.ensure_month_plan(sym.id)
                await mirror_latest_to_mongo(sym.id, records[-1])
                loaded += 1
        except Exception:
            pass
    return loaded


def _pe_pb_frames_from_store(symbol_id: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    rows = market_store.load_valuation_series(symbol_id)
    if not rows:
        return None, None
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    pe_df = frame.dropna(subset=["pe"])[["date", "pe"]] if "pe" in frame else None
    pb_df = frame.dropna(subset=["pb"])[["date", "pb"]] if "pb" in frame else None
    if pe_df is not None and pe_df.empty:
        pe_df = None
    if pb_df is not None and pb_df.empty:
        pb_df = None
    return pe_df, pb_df


def _persist_valuations(
    symbol: SymbolConfig,
    *,
    pe_history: pd.DataFrame | None,
    pb_history: pd.DataFrame | None,
    records: list[dict[str, Any]],
    valuation_source: str | None,
    source: str,
) -> None:
    if not valuation_source:
        return
    source_symbol = (
        symbol.valuation_proxy_label or f"{symbol.pe_symbol}市场代理"
        if symbol.valuation_proxy
        else symbol.index_code
    )
    quality = (
        "proxy"
        if symbol.valuation_proxy
        else "verified" if source != "mock" else "synthetic"
    )
    history_records: list[dict[str, Any]] = []
    pe_map: dict[str, float | None] = {}
    pb_map: dict[str, float | None] = {}
    if pe_history is not None and not pe_history.empty:
        for _, row in pe_history.iterrows():
            value = _f(row.get("pe"))
            if value is not None:
                pe_map[str(row["date"])] = value
    if pb_history is not None and not pb_history.empty:
        for _, row in pb_history.iterrows():
            value = _f(row.get("pb"))
            if value is not None:
                pb_map[str(row["date"])] = value
    for key in sorted(set(pe_map) | set(pb_map)):
        history_records.append(
            {"date": key, "pe": pe_map.get(key), "pb": pb_map.get(key)}
        )
    market_store.upsert_valuation_observations(
        symbol.id,
        history_records or records,
        source=valuation_source,
        source_symbol=source_symbol,
        quality_status=quality,
    )
    market_store.materialize_valuation_metrics(symbol.id, records)


def _valuation_source_for(
    symbol: SymbolConfig,
    pe_history: pd.DataFrame | None,
    pb_history: pd.DataFrame | None,
) -> str | None:
    has_history = (
        (pe_history is not None and not pe_history.empty)
        or (pb_history is not None and not pb_history.empty)
    )
    if not has_history:
        return None
    return (
        "akshare-legulegu-market-proxy"
        if symbol.valuation_proxy
        else "akshare-legulegu"
    )


def _refresh_valuation_only(
    symbol: SymbolConfig,
    ma_short: int,
    ma_long: int,
) -> str:
    """Refresh valuation history and rematerialize bars without pulling prices."""
    try:
        import akshare as ak

        pe_history, pb_history = fetch_akshare_valuations(ak, symbol)
    except Exception:
        pe_history = pb_history = None

    valuation_source = _valuation_source_for(symbol, pe_history, pb_history)
    if valuation_source is None and symbol.id in FREE_VALUATION_SYMBOLS:
        legacy = load_legacy_free_valuations(symbol)
        if legacy is not None:
            pe_history = legacy[["date", "pe"]].dropna() if "pe" in legacy else None
            pb_history = legacy[["date", "pb"]].dropna() if "pb" in legacy else None
            valuation_source = "legacy-akshare-legulegu"
    if valuation_source is None:
        raise RuntimeError(f"估值刷新失败: {symbol.id} ({symbol.name})")

    rows = market_store.load_records(symbol.id)
    if not rows:
        raise RuntimeError(f"估值刷新缺少行情底表: {symbol.id}")
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame = apply_valuation_asof(
        frame.drop(
            columns=["pe", "pb", "valuation_asof", "valuation_source"],
            errors="ignore",
        ),
        pe_history,
        pb_history,
        valuation_source=valuation_source,
    )
    frame = enrich_indicators(
        frame,
        ma_short,
        ma_long,
        pe_history=pe_history,
        pb_history=pb_history,
        valuation_window_years=valuation_window_years_for(symbol),
    )
    source = str(frame.iloc[-1].get("source") or "akshare")
    records = df_to_records(symbol.id, frame, source)
    _persist_valuations(
        symbol,
        pe_history=pe_history,
        pb_history=pb_history,
        records=records,
        valuation_source=valuation_source,
        source=source,
    )
    return valuation_source


def _sync_symbol_cpu(
    symbol: SymbolConfig,
    force_mock: bool,
    ma_short: int,
    ma_long: int,
    *,
    force_full: bool = False,
) -> tuple[list[dict[str, Any]], str, str | None, str]:
    """CPU/network sync. Returns (records_upserted, source, valuation_source, mode).

    mode:
      - full: empty warehouse or force_full
      - incremental: only fetch/write bars after warehouse latest
      - skipped: already at T-1 (no network)
    """
    pe_history = pb_history = None
    mode = "full"

    if force_mock:
        df = generate_mock_history(symbol, days=520)
        source = "mock"
        valuation_source = "mock"
        df["valuation_asof"] = df["date"]
        df["valuation_source"] = valuation_source
        pe_history = df[["date", "pe"]].dropna()
        pb_history = df[["date", "pb"]].dropna()
        df = enrich_indicators(
            df,
            ma_short,
            ma_long,
            pe_history=pe_history,
            pb_history=pb_history,
            valuation_window_years=valuation_window_years_for(symbol),
        )
        records = df_to_records(symbol.id, df, source)
        market_store.upsert_records(symbol.id, records)
        _persist_valuations(
            symbol,
            pe_history=pe_history,
            pb_history=pb_history,
            records=records,
            valuation_source=valuation_source,
            source=source,
        )
        market_store.ensure_month_plan(symbol.id, start_ym="1991-01")
        return records, source, valuation_source, mode

    latest = market_store.get_latest_bar(symbol.id)
    expected_t1 = market_store.expected_trading_t1().isoformat()
    bar_count = market_store.count_bars(symbol.id)
    min_seed = max(ma_long + 40, 180)

    price_fresh = bool(
        not force_full
        and latest
        and str(latest.get("date") or "") >= expected_t1
        and latest.get("ma_long") is not None
    )
    latest_valuation_asof = latest.get("valuation_asof") if latest else None
    valuation_lag = valuation_lag_sessions(latest_valuation_asof, expected_t1)
    valuation_fresh = (
        not symbol.valuation_enabled
        or valuation_lag is not None
        and valuation_lag <= MAX_VALUATION_LAG_SESSIONS
    )
    if price_fresh and valuation_fresh:
        return [], latest.get("source") or "warehouse", None, "skipped"
    if price_fresh:
        valuation_source = _refresh_valuation_only(symbol, ma_short, ma_long)
        return [], latest.get("source") or "warehouse", valuation_source, "incremental"

    use_incremental = (
        not force_full
        and latest is not None
        and bar_count >= min_seed
        and latest.get("date")
    )

    if use_incremental:
        warehouse_latest = date.fromisoformat(str(latest["date"])[:10])
        fetch_start = (warehouse_latest + timedelta(days=1)).strftime("%Y%m%d")
        lookback_start = (
            warehouse_latest - timedelta(days=INCREMENTAL_LOOKBACK_BARS * 2)
        ).isoformat()

        # Price only — PE/PB come from warehouse (no full valuation re-pull).
        fetched = fetch_akshare_index(
            symbol, start_date=fetch_start, with_valuation=False
        )
        local_rows = market_store.load_records_since(symbol.id, lookback_start)
        local_df = pd.DataFrame(local_rows)

        if fetched is not None and not local_df.empty:
            new_df, _, _ = fetched
            new_df = new_df[new_df["date"] > warehouse_latest].copy()
            if new_df.empty:
                # Provider has nothing newer than warehouse tip.
                if str(latest.get("date") or "") >= expected_t1:
                    return [], latest.get("source") or "warehouse", None, "skipped"
                # Behind calendar T-1 but provider empty — fall through to full.
            else:
                local_df["date"] = pd.to_datetime(local_df["date"]).dt.date
                for col in (
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "pe",
                    "pb",
                    "etf_close",
                ):
                    if col not in local_df.columns:
                        local_df[col] = np.nan
                local_part = local_df[
                    ["date", "open", "high", "low", "close", "volume", "etf_close"]
                ].copy()

                etf = fetch_akshare_etf_history(symbol, start_date=fetch_start)
                if etf is not None:
                    new_df = new_df.merge(etf, on="date", how="left")
                else:
                    new_df["etf_close"] = np.nan

                pe_history, pb_history = _pe_pb_frames_from_store(symbol.id)
                source = "akshare"
                valuation_source = None
                if (
                    symbol.id in FREE_VALUATION_SYMBOLS
                    and pe_history is not None
                    and pe_history["pe"].notna().sum() >= 20
                ):
                    valuation_source = (
                        "warehouse-legulegu-market-proxy"
                        if symbol.valuation_proxy
                        else "warehouse-legulegu"
                    )

                for col in ("pe", "pb"):
                    if col not in new_df.columns:
                        new_df[col] = np.nan

                combined = pd.concat([local_part, new_df], ignore_index=True, sort=False)
                combined = combined.drop_duplicates(subset=["date"], keep="last")
                combined = combined.sort_values("date").reset_index(drop=True)
                if pe_history is not None or pb_history is not None:
                    combined = apply_valuation_asof(
                        combined.drop(columns=["pe", "pb"], errors="ignore"),
                        pe_history,
                        pb_history,
                        valuation_source=valuation_source,
                    )

                combined = enrich_indicators(
                    combined,
                    ma_short,
                    ma_long,
                    pe_history=pe_history,
                    pb_history=pb_history,
                    valuation_window_years=valuation_window_years_for(symbol),
                )
                # Only write newly fetched sessions (缺几个加几个).
                to_write = combined[combined["date"] > warehouse_latest]
                records = df_to_records(symbol.id, to_write, source)
                market_store.upsert_records(symbol.id, records)
                # Do not rewrite full valuation history on incremental price sync.
                if valuation_source:
                    market_store.materialize_valuation_metrics(symbol.id, records)
                logger.info(
                    "incremental sync %s: +%d bars after %s",
                    symbol.id,
                    len(records),
                    warehouse_latest,
                )
                return records, source, valuation_source, "incremental"

    # Full path: first seed or force_full / incremental fallback
    mode = "full"
    fetched = fetch_akshare_index(symbol, start_date=None, with_valuation=True)
    if fetched is None:
        raise RuntimeError(f"实时行情拉取失败: {symbol.id} ({symbol.name})")
    df, pe_history, pb_history = fetched
    source = "akshare"
    valuation_source = (
        (
            "akshare-legulegu-market-proxy"
            if symbol.valuation_proxy
            else "akshare-legulegu"
        )
        if symbol.id in FREE_VALUATION_SYMBOLS
        and (
            (pe_history is not None and pe_history["pe"].notna().sum() >= 20)
            or ("pe" in df.columns and df["pe"].notna().sum() >= 20)
        )
        else None
    )
    if symbol.id in FREE_VALUATION_SYMBOLS and valuation_source is None:
        legacy = load_legacy_free_valuations(symbol)
        if legacy is not None:
            pe_history = legacy[["date", "pe"]].dropna() if "pe" in legacy else None
            pb_history = legacy[["date", "pb"]].dropna() if "pb" in legacy else None
            df = apply_valuation_asof(
                df.drop(columns=["pe", "pb"], errors="ignore"),
                pe_history,
                pb_history,
                valuation_source="legacy-akshare-legulegu",
            )
            valuation_source = "legacy-akshare-legulegu"
    if "valuation_source" in df.columns:
        df["valuation_source"] = valuation_source

    etf = fetch_akshare_etf_history(symbol)
    if etf is not None:
        df = df.merge(etf, on="date", how="left")

    df = enrich_indicators(
        df,
        ma_short,
        ma_long,
        pe_history=pe_history,
        pb_history=pb_history,
        valuation_window_years=valuation_window_years_for(symbol),
    )
    records = df_to_records(symbol.id, df, source)
    market_store.upsert_records(symbol.id, records)
    _persist_valuations(
        symbol,
        pe_history=pe_history,
        pb_history=pb_history,
        records=records,
        valuation_source=valuation_source,
        source=source,
    )
    market_store.ensure_month_plan(symbol.id, start_ym="1991-01")
    return records, source, valuation_source, mode


async def sync_symbol(
    symbol: SymbolConfig,
    use_mock: bool | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    force_mock = settings.use_mock_data if use_mock is None else use_mock
    cfg = load_app_config()

    from app.db import get_user_settings

    try:
        user = await get_user_settings()
        ma_short = user.ma_short or cfg.defaults.ma_short
        ma_long = user.ma_long or cfg.defaults.ma_long
    except Exception:
        ma_short = cfg.defaults.ma_short
        ma_long = cfg.defaults.ma_long

    records, source, valuation_source, mode = await asyncio.to_thread(
        _sync_symbol_cpu,
        symbol,
        force_mock,
        ma_short,
        ma_long,
        force_full=force,
    )

    if mode == "skipped":
        latest = market_store.get_latest_bar(symbol.id) or {}
        if latest:
            await mirror_latest_to_mongo(symbol.id, latest)
        logger.info(
            "skip sync %s — warehouse fresh through %s",
            symbol.id,
            latest.get("date"),
        )
        return {
            "symbol": symbol.id,
            "source": source,
            "valuation_source": valuation_source,
            "mode": "skipped",
            "rows_added": 0,
            "rows": market_store.count_bars(symbol.id),
            "latest_date": latest.get("date"),
            "latest_close": latest.get("close"),
            "stored_in": str(market_store.get_db_path()),
            "ma_short": ma_short,
            "ma_long": ma_long,
        }

    if records:
        await mirror_latest_to_mongo(symbol.id, records[-1])
    tip = market_store.get_latest_bar(symbol.id) or {}
    return {
        "symbol": symbol.id,
        "source": source,
        "valuation_source": valuation_source,
        "mode": mode,
        "rows_added": len(records),
        "rows": market_store.count_bars(symbol.id),
        "latest_date": tip.get("date") or (records[-1]["date"] if records else None),
        "latest_close": tip.get("close") or (records[-1]["close"] if records else None),
        "stored_in": str(market_store.get_db_path()),
        "ma_short": ma_short,
        "ma_long": ma_long,
    }


async def sync_all(
    use_mock: bool | None = None,
    *,
    force: bool = False,
    concurrency: int = SYNC_CONCURRENCY,
) -> dict[str, Any]:
    cfg = load_app_config()
    settings = get_settings()
    force_mock = settings.use_mock_data if use_mock is None else use_mock
    purged = market_store.purge_symbols_not_in({s.id for s in cfg.symbols})

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(sym: SymbolConfig) -> dict[str, Any]:
        async with sem:
            try:
                return await sync_symbol(sym, use_mock=force_mock, force=force)
            except Exception as exc:
                logger.exception("sync failed for %s", sym.id)
                return {
                    "symbol": sym.id,
                    "source": "error",
                    "mode": "error",
                    "valuation_source": None,
                    "rows": 0,
                    "latest_date": None,
                    "latest_close": None,
                    "error": str(exc),
                    "stored_in": str(market_store.get_db_path()),
                }

    results = list(await asyncio.gather(*[_one(sym) for sym in cfg.symbols]))

    warning = None
    if force_mock:
        warning = "当前为 USE_MOCK_DATA=true，未使用实时行情"
    elif any(r.get("source") == "error" for r in results):
        failed = [r["symbol"] for r in results if r.get("source") == "error"]
        warning = "同步失败：" + "、".join(failed)
    elif any(r.get("source") == "mock" for r in results):
        warning = "部分或全部行情回退到 mock（akshare 拉取失败）"
    else:
        valuation_required = {s.id for s in cfg.symbols if s.valuation_enabled}
        valuation_missing = [
            r["symbol"]
            for r in results
            if r["symbol"] in valuation_required
            and r.get("mode") == "full"
            and not r.get("valuation_source")
        ]
        if valuation_missing:
            warning = (
                "以下指数免费估值源不可用，策略将安全暂停："
                + "、".join(valuation_missing)
            )

    skipped = sum(1 for r in results if r.get("mode") == "skipped")
    incremental = sum(1 for r in results if r.get("mode") == "incremental")
    fetched = sum(1 for r in results if r.get("mode") in {"full", "incremental"})
    rows_added = sum(int(r.get("rows_added") or 0) for r in results)
    if warning is None and skipped and fetched == 0:
        warning = f"行情仓已是最新（T-1），已跳过 {skipped} 个标的的网络拉取"
    elif warning is None and incremental and not any(
        r.get("mode") == "full" for r in results
    ):
        warning = f"增量同步：补齐 {rows_added} 根 K 线（{incremental} 个标的）"

    live_ok = all(
        r.get("source") in {"akshare", "warehouse"} or r.get("mode") == "skipped"
        for r in results
    ) and not any(r.get("source") == "error" for r in results)

    return {
        "synced_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "results": results,
        "purged": purged,
        "warning": warning,
        "live": live_ok and not force_mock,
        "skipped": skipped,
        "incremental": incremental,
        "fetched": fetched,
        "rows_added": rows_added,
        "force": force,
        "data_status": market_store.data_status(),
    }


async def backfill_idle_chunk(months: int = 1) -> dict[str, Any]:
    """Idle-time job: walk pending months and verify coverage in SQLite.

    Does not re-hit akshare for every empty early month (pre-listing / holidays).
    A full sync is triggered at most once per chunk if the symbol has no bars at all.
    """
    cfg = load_app_config()
    for sym in cfg.symbols:
        market_store.ensure_month_plan(sym.id, start_ym="1991-01")

    done: list[dict[str, Any]] = []
    synced_this_chunk: set[str] = set()

    for _ in range(max(1, months)):
        nxt = market_store.next_pending_month()
        if not nxt:
            break
        symbol, ym = nxt
        with market_store.connect() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND date LIKE ?",
                (symbol, f"{ym}%"),
            ).fetchone()[0]
            earliest = conn.execute(
                "SELECT MIN(date) FROM market_bars WHERE symbol=?",
                (symbol,),
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(*) FROM market_bars WHERE symbol=?",
                (symbol,),
            ).fetchone()[0]

        if cnt > 0:
            market_store.mark_month(symbol, ym, "done", rows=cnt)
            done.append({"symbol": symbol, "year_month": ym, "rows": cnt, "status": "done"})
            continue

        # No bars in this month.
        if total == 0 and symbol not in synced_this_chunk:
            # Warehouse empty for symbol — one sync attempt
            sym = next(s for s in cfg.symbols if s.id == symbol)
            try:
                await sync_symbol(sym, use_mock=False)
                synced_this_chunk.add(symbol)
                with market_store.connect() as conn:
                    cnt2 = conn.execute(
                        "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND date LIKE ?",
                        (symbol, f"{ym}%"),
                    ).fetchone()[0]
                market_store.mark_month(symbol, ym, "done", rows=cnt2)
                done.append(
                    {
                        "symbol": symbol,
                        "year_month": ym,
                        "rows": cnt2,
                        "status": "filled" if cnt2 else "empty",
                    }
                )
            except Exception as e:
                market_store.mark_month(symbol, ym, "error", error=str(e))
                done.append(
                    {"symbol": symbol, "year_month": ym, "status": "error", "error": str(e)}
                )
                break
            continue

        # Already have history, but this month has 0 rows (pre-listing or holidays)
        if earliest and earliest[:7] > ym:
            market_store.mark_month(symbol, ym, "done", rows=0)
            done.append(
                {"symbol": symbol, "year_month": ym, "rows": 0, "status": "pre_listing"}
            )
        else:
            market_store.mark_month(symbol, ym, "done", rows=0)
            done.append(
                {"symbol": symbol, "year_month": ym, "rows": 0, "status": "empty"}
            )

    return {
        "processed": done,
        "data_status": market_store.data_status(),
    }


async def get_signal_market(symbol: str, signal_date: str | None = None) -> dict[str, Any] | None:
    """Bar used for strategy decisions — previous trading day (T-1)."""
    as_of = signal_date or market_store.resolve_signal_date()
    if not as_of:
        return None
    return market_store.get_latest_bar(symbol, on_or_before=as_of)


async def get_market_series(symbol: str, limit: int = 365) -> list[dict[str, Any]]:
    return market_store.load_records(symbol, limit=limit)
