from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import (
    ensure_indexes,
    get_db,
    get_portfolio,
    get_user_settings,
    load_app_config,
    save_portfolio,
    save_user_settings,
    seed_symbols_and_settings,
)
from app.models import Portfolio, TradeInput, UserSettings
from app.services.engine import compute_dashboard
from app.services.market_data import (
    backfill_idle_chunk,
    get_market_series,
    hydrate_db_from_disk_cache,
)
from app.services.sync_runner import sync_all_isolated
from app.services import market_store


_sync_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    market_store.ensure_store()
    await ensure_indexes()
    await seed_symbols_and_settings()
    # Warm from SQLite warehouse so /dashboard/today stays fast
    await hydrate_db_from_disk_cache()
    yield


app = FastAPI(title="A股宽基定投投顾看板", version="0.1.0", lifespan=lifespan)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/symbols")
async def symbols():
    cfg = load_app_config()
    return {"symbols": [s.model_dump() for s in cfg.symbols]}


@app.get("/api/dashboard/today")
async def dashboard_today():
    """Read-through cache: serve the computed payload for the current T-1
    signal date when present; only recompute when missing or invalidated."""
    signal_date = market_store.resolve_signal_date()
    if signal_date:
        db = get_db()
        doc = await db.signals_daily.find_one({"date": signal_date})
        if doc:
            doc.pop("_id", None)
            return doc
    return await compute_dashboard()


@app.get("/api/signals/history")
async def signals_history(limit: int = Query(default=30, ge=1, le=365)):
    """Review timeline (R6): recent daily snapshots + forward returns.

    Registered before ``/api/signals/{date}`` so ``history`` is not captured
    as a date path parameter.
    """
    from app.services.review import forward_returns_many

    db = get_db()
    docs = (
        await db.signals_daily.find()
        .sort("date", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    dates = [doc.get("date") for doc in docs if doc.get("date")]
    forwards = forward_returns_many(dates)
    out = []
    for doc in docs:
        doc.pop("_id", None)
        date = doc.get("date")
        counts = {"buy": 0, "pause": 0, "reduce": 0, "hold": 0}
        for item in doc.get("items") or []:
            action = item.get("action", "hold")
            counts[action] = counts.get(action, 0) + 1
        out.append(
            {
                "date": date,
                "execution_today": bool(doc.get("execution_today", False)),
                "total_buy_amount": round(float(doc.get("total_buy_amount") or 0), 2),
                "action_counts": counts,
                "warning": doc.get("warning"),
                "forward": forwards.get(date, {}) if date else {},
            }
        )
    return {"history": out}


@app.get("/api/signals/{date}")
async def signals_by_date(date: str):
    """Historical or T-1 signals. Date is always clamped to <= warehouse T-1."""
    from app.db import get_db
    from app.services import market_store

    t1 = market_store.resolve_signal_date()
    clamped = min(date, t1) if t1 else date
    db = get_db()
    if clamped == date:
        doc = await db.signals_daily.find_one({"date": clamped})
        if doc:
            doc.pop("_id", None)
            return doc
    return await compute_dashboard(as_of=clamped)


@app.get("/api/market/{symbol}")
async def market(symbol: str, limit: int = 365):
    series = await get_market_series(symbol.upper(), limit=limit)
    return {"symbol": symbol.upper(), "series": series}


async def _invalidate_dashboard_cache() -> None:
    """Drop today's cached dashboard so the next read recomputes.

    Historical ``signals_daily`` rows are kept for the /review timeline.
    """
    t1 = market_store.resolve_signal_date()
    if not t1:
        return
    db = get_db()
    await db.signals_daily.delete_one({"date": t1})


@app.get("/api/portfolio")
async def read_portfolio():
    return await get_portfolio()


@app.put("/api/portfolio")
async def update_portfolio(portfolio: Portfolio):
    saved = await save_portfolio(portfolio)
    await _invalidate_dashboard_cache()
    return saved


@app.post("/api/portfolio/trades")
async def apply_trade_endpoint(trade: TradeInput):
    """Reconcile the tracked position with a real executed trade (R2).

    deposit / buy / sell / dividend all update shares, average cost, cash and
    accumulated dividends; the change is persisted and cached signals dropped.
    """
    from app.services.ledger import LedgerError, apply_trade

    portfolio = await get_portfolio()
    work = portfolio.model_copy(deep=True)
    t1 = market_store.resolve_signal_date()

    def _price(symbol: str) -> float | None:
        bar = (
            market_store.get_latest_bar(symbol, on_or_before=t1) if t1 else None
        )
        if bar and bar.get("etf_close") is not None:
            return float(bar["etf_close"])
        if bar and bar.get("close") is not None:
            return float(bar["close"])
        return None

    try:
        updated, record = apply_trade(work, trade, _price)
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await save_portfolio(updated)
    await _invalidate_dashboard_cache()
    return {"portfolio": updated, "trade": record}


@app.get("/api/settings")
async def read_settings():
    return await get_user_settings()


@app.put("/api/settings")
async def update_settings(body: UserSettings):
    saved = await save_user_settings(body)
    await _invalidate_dashboard_cache()
    return saved


@app.post("/api/jobs/sync")
async def job_sync(use_mock: bool | None = None, force: bool = False):
    """Sync market data in an isolated subprocess (survives mini_racer abort)."""
    async with _sync_lock:
        result = await sync_all_isolated(use_mock=use_mock, force=force)
        await _invalidate_dashboard_cache()
        return result


@app.post("/api/jobs/backfill")
async def job_backfill(months: int = 3):
    """Idle/manual monthly backfill into local warehouse."""
    result = await backfill_idle_chunk(months=months)
    await _invalidate_dashboard_cache()
    return result


@app.get("/api/data/status")
async def data_status():
    return market_store.data_status()


@app.get("/api/data/overview")
async def data_overview():
    """Small aggregate payload suitable for a dashboard."""
    return await asyncio.to_thread(market_store.data_overview)


@app.get("/api/data/rows")
async def data_rows(
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=20, le=200),
):
    """Cursor-paginated raw rows; never loads the full market table."""
    try:
        return await asyncio.to_thread(
            market_store.query_market_page,
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
            cursor=cursor,
            limit=limit,
        )
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="无效的分页游标") from exc
