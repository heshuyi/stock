from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.config import get_settings
from app.db import get_db, load_app_config
from app.models import SymbolConfig
from app.services import market_store

VALUATION_WINDOW_DAYS = 5 * 252
FULL_VALUATION_WINDOW_DAYS = 100 * 365
FREE_VALUATION_SYMBOLS = {"HS300", "ZZ500", "CYB200", "KCB50", "SZ50"}


def valuation_window_days_for(symbol: SymbolConfig) -> int:
    window = getattr(symbol.strategy_profile, "percentile_window", "5y")
    if window == "full":
        return FULL_VALUATION_WINDOW_DAYS
    return VALUATION_WINDOW_DAYS


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


def fetch_akshare_etf_history(symbol: SymbolConfig) -> pd.DataFrame | None:
    """Fetch unadjusted ETF closes for cost-basis and take-profit calculations."""
    frame = None
    try:
        import akshare as ak

        frame = ak.fund_etf_hist_em(
            symbol=symbol.etf_code,
            period="daily",
            start_date="19900101",
            end_date=date.today().strftime("%Y%m%d"),
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
    return frame[["date", "etf_close"]].dropna().sort_values("date")


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
) -> pd.DataFrame:
    """Attach PE/PB with backward as-of fill so monthly proxies cover daily bars."""
    out = df.sort_values("date").copy()
    out["_dt"] = pd.to_datetime(out["date"])

    def _asof_col(source: pd.DataFrame | None, col: str) -> pd.Series:
        if source is None or source.empty or col not in source.columns:
            return pd.Series(np.nan, index=out.index)
        hist = source.dropna(subset=[col]).sort_values("date").copy()
        if hist.empty:
            return pd.Series(np.nan, index=out.index)
        hist["_dt"] = pd.to_datetime(hist["date"])
        merged = pd.merge_asof(
            out[["_dt"]],
            hist[["_dt", col]],
            on="_dt",
            direction="backward",
        )
        return merged[col]

    out["pe"] = _asof_col(pe_df, "pe")
    out["pb"] = _asof_col(pb_df, "pb")
    return out.drop(columns=["_dt"])


def _percentile_against_history(
    dates: list[Any],
    values: np.ndarray,
    history: pd.DataFrame | None,
    value_col: str,
    window_days: int = VALUATION_WINDOW_DAYS,
) -> list[float]:
    """Rank each value against the provider history ending on that date."""
    if history is None or history.empty or value_col not in history.columns:
        return [float("nan")] * len(values)

    hist = history.dropna(subset=[value_col]).sort_values("date").copy()
    if hist.empty:
        return [float("nan")] * len(values)

    hist_dates = pd.to_datetime(hist["date"]).to_numpy(dtype="datetime64[ns]")
    hist_vals = hist[value_col].to_numpy(dtype=float)
    out: list[float] = []
    window = np.timedelta64(window_days, "D")
    for raw_date, value in zip(dates, values):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            out.append(float("nan"))
            continue
        end = np.datetime64(pd.Timestamp(raw_date).to_datetime64())
        start = end - window
        mask = (hist_dates >= start) & (hist_dates <= end)
        window_vals = hist_vals[mask]
        if len(window_vals) < 5:
            out.append(0.5)
        else:
            out.append(float((window_vals <= float(value)).mean()))
    return out


def fetch_akshare_index(
    symbol: SymbolConfig,
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None] | None:
    """Fetch live index OHLCV + PE/PB history from akshare.

    Returns (bars, pe_history, pb_history). History frames keep the full
    provider series so proxy percentiles can use pre-listing observations.
    """
    try:
        import akshare as ak

        df = ak.stock_zh_index_daily(symbol=symbol.akshare_symbol)
        if df is None or df.empty:
            df = ak.index_zh_a_hist(
                symbol=symbol.index_code, period="daily", start_date="19910101"
            )
        if df is None or df.empty:
            return None

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
        # Keep the complete real series returned by the provider. Each index
        # naturally starts at its own inception/base date; no synthetic splice.
        df = df.sort_values("date").reset_index(drop=True)

        # Index valuation is preferred. A broad-market proxy is only used when
        # the symbol explicitly opts in and remains labelled as proxy data.
        pe_df, pb_df = fetch_akshare_valuations(ak, symbol)
        df = apply_valuation_asof(df, pe_df, pb_df)
        return (
            df[["date", "open", "high", "low", "close", "volume", "pe", "pb"]],
            pe_df,
            pb_df,
        )
    except Exception:
        return None


def enrich_indicators(
    df: pd.DataFrame,
    ma_short: int = 60,
    ma_long: int = 120,
    pe_history: pd.DataFrame | None = None,
    pb_history: pd.DataFrame | None = None,
    valuation_window_days: int = VALUATION_WINDOW_DAYS,
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
            window_days=valuation_window_days,
        )
    else:
        window = min(len(out), valuation_window_days)
        arr = out["pe"].to_numpy(dtype=float)
        vals = []
        for i in range(len(arr)):
            if np.isnan(arr[i]):
                vals.append(np.nan)
                continue
            start = max(0, i - window + 1)
            hist = arr[start : i + 1]
            hist = hist[~np.isnan(hist)]
            if len(hist) < 5:
                vals.append(0.5)
            else:
                vals.append(float((hist <= hist[-1]).mean()))
        out["pe_percentile"] = vals

    if pb_history is not None and not pb_history.empty:
        out["pb_percentile"] = _percentile_against_history(
            dates,
            out["pb"].to_numpy(dtype=float),
            pb_history,
            "pb",
            window_days=valuation_window_days,
        )
    else:
        window = min(len(out), valuation_window_days)
        arr = out["pb"].to_numpy(dtype=float)
        vals = []
        for i in range(len(arr)):
            if np.isnan(arr[i]):
                vals.append(np.nan)
                continue
            start = max(0, i - window + 1)
            hist = arr[start : i + 1]
            hist = hist[~np.isnan(hist)]
            if len(hist) < 5:
                vals.append(0.5)
            else:
                vals.append(float((hist <= hist[-1]).mean()))
        out["pb_percentile"] = vals
    return out


def _f(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
                "source": record.get("source"),
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


def _sync_symbol_cpu(
    symbol: SymbolConfig,
    force_mock: bool,
    ma_short: int,
    ma_long: int,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """CPU/network heavy sync work — run in a worker thread."""
    pe_history = pb_history = None
    if not force_mock:
        fetched = fetch_akshare_index(symbol)
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

        # The free LG endpoint is intermittent. Bootstrap from previously
        # verified observations instead of replacing valuation history with NULL.
        if symbol.id in FREE_VALUATION_SYMBOLS and valuation_source is None:
            legacy = load_legacy_free_valuations(symbol)
            if legacy is not None:
                pe_history = legacy[["date", "pe"]].dropna() if "pe" in legacy else None
                pb_history = legacy[["date", "pb"]].dropna() if "pb" in legacy else None
                df = apply_valuation_asof(
                    df.drop(columns=["pe", "pb"], errors="ignore"),
                    pe_history,
                    pb_history,
                )
                valuation_source = "legacy-akshare-legulegu"

        etf = fetch_akshare_etf_history(symbol)
        if etf is not None:
            df = df.merge(etf, on="date", how="left")
    else:
        df = generate_mock_history(symbol, days=520)
        source = "mock"
        valuation_source = "mock"
        pe_history = df[["date", "pe"]].dropna()
        pb_history = df[["date", "pb"]].dropna()

    df = enrich_indicators(
        df,
        ma_short,
        ma_long,
        pe_history=pe_history,
        pb_history=pb_history,
        valuation_window_days=valuation_window_days_for(symbol),
    )
    records = df_to_records(symbol.id, df, source)
    market_store.upsert_records(symbol.id, records)
    if valuation_source:
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
        # Persist the full provider history (including pre-listing months)
        # so proxy percentiles remain auditable.
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
    market_store.ensure_month_plan(symbol.id, start_ym="1991-01")
    return records, source, valuation_source


async def sync_symbol(symbol: SymbolConfig, use_mock: bool | None = None) -> dict[str, Any]:
    import asyncio

    settings = get_settings()
    force_mock = settings.use_mock_data if use_mock is None else use_mock
    cfg = load_app_config()

    # Prefer user settings MA if available (async mongo)
    from app.db import get_user_settings

    try:
        user = await get_user_settings()
        ma_short = user.ma_short or cfg.defaults.ma_short
        ma_long = user.ma_long or cfg.defaults.ma_long
    except Exception:
        ma_short = cfg.defaults.ma_short
        ma_long = cfg.defaults.ma_long

    records, source, valuation_source = await asyncio.to_thread(
        _sync_symbol_cpu, symbol, force_mock, ma_short, ma_long
    )
    if records:
        await mirror_latest_to_mongo(symbol.id, records[-1])

    return {
        "symbol": symbol.id,
        "source": source,
        "valuation_source": valuation_source,
        "rows": len(records),
        "latest_date": records[-1]["date"] if records else None,
        "latest_close": records[-1]["close"] if records else None,
        "stored_in": str(market_store.get_db_path()),
        "ma_short": ma_short,
        "ma_long": ma_long,
    }


async def sync_all(use_mock: bool | None = None) -> dict[str, Any]:
    cfg = load_app_config()
    settings = get_settings()
    force_mock = settings.use_mock_data if use_mock is None else use_mock
    purged = market_store.purge_symbols_not_in({s.id for s in cfg.symbols})
    results = []
    warning = None
    for sym in cfg.symbols:
        r = await sync_symbol(sym, use_mock=force_mock)
        results.append(r)
        if r["source"] == "mock":
            warning = "部分或全部行情回退到 mock（akshare 拉取失败）"
    if force_mock:
        warning = "当前为 USE_MOCK_DATA=true，未使用实时行情"
    else:
        valuation_required = {s.id for s in cfg.symbols if s.valuation_enabled}
        valuation_missing = [
            r["symbol"]
            for r in results
            if r["symbol"] in valuation_required and not r.get("valuation_source")
        ]
        if valuation_missing:
            warning = (
                "以下指数免费估值源不可用，策略将安全暂停："
                + "、".join(valuation_missing)
            )
        elif all(r["source"] == "akshare" for r in results):
            warning = None
    if not force_mock and warning is None and all(
        r["source"] == "akshare" for r in results
    ):
        warning = None
    return {
        "synced_at": datetime.utcnow().isoformat() + "Z",
        "results": results,
        "purged": purged,
        "warning": warning,
        "live": all(r["source"] == "akshare" for r in results),
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


async def get_latest_market(symbol: str) -> dict[str, Any] | None:
    """Latest bar for charts / status (may include today if already synced)."""
    return market_store.get_latest_bar(symbol)


async def get_signal_market(symbol: str, signal_date: str | None = None) -> dict[str, Any] | None:
    """Bar used for strategy decisions — previous trading day (T-1)."""
    as_of = signal_date or market_store.resolve_signal_date()
    if not as_of:
        return None
    return market_store.get_latest_bar(symbol, on_or_before=as_of)


async def get_market_series(symbol: str, limit: int = 365) -> list[dict[str, Any]]:
    return market_store.load_records(symbol, limit=limit)
